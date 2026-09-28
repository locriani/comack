#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"

#include "config.h"
#include "secrets.h"
#include "mic_sampler.h"
#include "epd_display.h"
#include "microlink.h"
#include "ws_client.h"

static const char *TAG = "COMACK";

// Hardware drivers
static MicSampler mic;
static EPDDisplay epd;

// MicroLink Tailscale Handle
static microlink_t *ml = NULL;

// PSRAM Buffers
static int16_t *audioChunkBuffer = NULL;
static uint8_t *epdFrameBuffer = NULL;

// Synchronization
static EventGroupHandle_t app_event_group;
#define WIFI_CONNECTED_BIT      BIT0
#define TAILSCALE_CONNECTED_BIT BIT1

// Wi-Fi rotation state
static int current_wifi_idx = 0;

/* ============================================================================
 * Tailscale Callbacks
 * ========================================================================== */

static void on_tailscale_state_change(microlink_t *ml_handle, microlink_state_t state, void *user_data) {
    const char *state_names[] = {
        "IDLE", "WIFI_WAIT", "CONNECTING", "REGISTERING",
        "CONNECTED", "RECONNECTING", "ERROR"
    };
    const char *name = (state < sizeof(state_names)/sizeof(state_names[0])) ? state_names[state] : "UNKNOWN";
    ESP_LOGI(TAG, "Tailscale state -> %s", name);

    if (state == ML_STATE_CONNECTED) {
        uint32_t ip = microlink_get_vpn_ip(ml_handle);
        char ip_str[16];
        microlink_ip_to_str(ip, ip_str);
        ESP_LOGI(TAG, "==================================================");
        ESP_LOGI(TAG, "  🎉 TAILSCALE REGISTERED! Node IP: %s", ip_str);
        ESP_LOGI(TAG, "==================================================");
        xEventGroupSetBits(app_event_group, TAILSCALE_CONNECTED_BIT);
    }
}

static void on_tailscale_peer_update(microlink_t *ml_handle, const microlink_peer_info_t *peer, void *user_data) {
    char ip_str[16];
    microlink_ip_to_str(peer->vpn_ip, ip_str);
    ESP_LOGI(TAG, "Tailscale Peer: %s (%s) online=%d direct=%d",
             peer->hostname, ip_str, peer->online, peer->direct_path);
}

/* ============================================================================
 * Wi-Fi Event Handler & Multi-AP Support
 * ========================================================================== */

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnected. Rotating to next network...");
        current_wifi_idx = (current_wifi_idx + 1) % WIFI_NETWORK_COUNT;
        wifi_config_t wifi_cfg = {};
        strncpy((char *)wifi_cfg.sta.ssid, WIFI_NETWORKS[current_wifi_idx].ssid, sizeof(wifi_cfg.sta.ssid) - 1);
        strncpy((char *)wifi_cfg.sta.password, WIFI_NETWORKS[current_wifi_idx].pass, sizeof(wifi_cfg.sta.password) - 1);
        esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Wi-Fi connected to '%s'! Local IP: " IPSTR,
                 WIFI_NETWORKS[current_wifi_idx].ssid,
                 IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(app_event_group, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_cfg = {};
    strncpy((char *)wifi_cfg.sta.ssid, WIFI_NETWORKS[0].ssid, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, WIFI_NETWORKS[0].pass, sizeof(wifi_cfg.sta.password) - 1);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE)); // Low latency for WireGuard

    ESP_LOGI(TAG, "Searching for Wi-Fi network: %s...", WIFI_NETWORKS[0].ssid);
}

/* ============================================================================
 * Comack Network Tasks (Audio Streamer + Comic Downloader)
 * ========================================================================== */

// Resolve the server via Tailscale MagicDNS (falls back to a literal IP). 0 = not yet known.
static uint32_t resolve_server(void) {
    uint32_t ip = microlink_resolve(ml, COMACK_SERVER_HOST);
    return ip ? ip : microlink_parse_ip(COMACK_SERVER_HOST);
}

// Bodiless HTTP request over the tunnel; copies up to `cap` body bytes into `out`.
// Returns body bytes read, -1 if the connection failed.
static int http_request(uint32_t ip, const char *method, const char *path, uint8_t *out, size_t cap) {
    microlink_tcp_socket_t *sock = microlink_tcp_connect(ml, ip, COMACK_SERVER_PORT, 10000);
    if (!sock) return -1;

    char req[192];
    int n = snprintf(req, sizeof(req), "%s %s HTTP/1.1\r\nHost: %s:%d\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                     method, path, COMACK_SERVER_HOST, COMACK_SERVER_PORT);
    microlink_tcp_send(sock, req, n);

    size_t body = 0;
    int sep = 0; // bytes of "\r\n\r\n" matched so far; 4 = inside body
    while (body < cap) {
        uint8_t chunk[1024];
        int r = microlink_tcp_recv(sock, chunk, sizeof(chunk), 5000);
        if (r <= 0) break;
        int i = 0;
        for (; sep < 4 && i < r; i++) {
            sep = (chunk[i] == "\r\n\r\n"[sep]) ? sep + 1 : (chunk[i] == '\r' ? 1 : 0);
        }
        size_t take = ((size_t)(r - i) > cap - body) ? cap - body : (size_t)(r - i);
        memcpy(out + body, chunk + i, take);
        body += take;
    }
    microlink_tcp_close(sock);
    return (int)body;
}

static bool download_and_refresh_comic(uint32_t ip, int issue) {
    char path[40];
    snprintf(path, sizeof(path), "/api/comics/%d/bin", issue);
    ESP_LOGI(TAG, "Fetching comic #%d from %s...", issue, COMACK_SERVER_HOST);
    int got = http_request(ip, "GET", path, epdFrameBuffer, EPD_BUFFER_BYTES);
    if (got != EPD_BUFFER_BYTES) {
        ESP_LOGW(TAG, "Incomplete comic binary: %d / %d bytes", got, EPD_BUFFER_BYTES);
        return false;
    }
    uint32_t black = 0;
    for (int i = 0; i < EPD_BUFFER_BYTES; i++) black += __builtin_popcount((uint8_t)~epdFrameBuffer[i]);
    ESP_LOGI(TAG, "Comic binary received (%u/%d black pixels, first bytes %02x %02x %02x %02x). Updating e-Paper!",
             (unsigned)black, EPD_WIDTH * EPD_HEIGHT, epdFrameBuffer[0], epdFrameBuffer[1], epdFrameBuffer[2], epdFrameBuffer[3]);
    epd.displayFrame(epdFrameBuffer);
    return true;
}

static void audio_streaming_task(void *pvParameters) {
    ESP_LOGI(TAG, "Audio streaming task running on Core 0.");
    microlink_tcp_socket_t *ws = NULL;
    uint32_t chunks = 0;
    int16_t peak = 0;

    while (true) {
        xEventGroupWaitBits(app_event_group, TAILSCALE_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

        if (!ws) {
            uint32_t ip = resolve_server();
            if (ip) ws = ws_connect(ml, ip, COMACK_SERVER_PORT, COMACK_SERVER_HOST, "/ws/audio");
            if (!ws) {
                vTaskDelay(pdMS_TO_TICKS(2000));
                continue;
            }
            ESP_LOGI(TAG, "Audio WebSocket connected.");
        }

        // Sample audio from onboard PDM microphone and stream it to the server
        size_t samples = mic.readSamples(audioChunkBuffer, AUDIO_CHUNK_SAMPLES);
        if (samples > 0) {
            for (size_t i = 0; i < samples; i++) {
                int16_t a = audioChunkBuffer[i] < 0 ? -audioChunkBuffer[i] : audioChunkBuffer[i];
                if (a > peak) peak = a;
            }
            if (!ws_send_binary(ws, audioChunkBuffer, samples * sizeof(int16_t))) {
                ESP_LOGW(TAG, "Audio WebSocket dropped; reconnecting.");
                microlink_tcp_close(ws);
                ws = NULL;
            } else if (++chunks % 50 == 0) { // ~5s
                ESP_LOGI(TAG, "Audio: %u chunks sent, peak=%d", (unsigned)chunks, peak);
                peak = 0;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

/* ============================================================================
 * Main Application Entry Point
 * ========================================================================== */

extern "C" void app_main(void) {
    ESP_LOGI(TAG, "==========================================");
    ESP_LOGI(TAG, "  COMACK — reTerminal E1001 Display App   ");
    ESP_LOGI(TAG, "  Native Tailscale Node with MicroLink   ");
    ESP_LOGI(TAG, "==========================================");

    // 1. Initialize NVS (required for Wi-Fi and Tailscale key generation)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    app_event_group = xEventGroupCreate();

    // 2. Allocate buffers in PSRAM
    audioChunkBuffer = (int16_t*)heap_caps_malloc(AUDIO_CHUNK_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    epdFrameBuffer = (uint8_t*)heap_caps_malloc(EPD_BUFFER_BYTES, MALLOC_CAP_SPIRAM);

    if (!audioChunkBuffer || !epdFrameBuffer) {
        ESP_LOGE(TAG, "Failed to allocate PSRAM buffers!");
    } else {
        ESP_LOGI(TAG, "8MB PSRAM buffers allocated successfully (Audio: %d B, EPD: %d B)",
                 (int)(AUDIO_CHUNK_SAMPLES * sizeof(int16_t)), EPD_BUFFER_BYTES);
    }

    // 3. Initialize hardware peripherals
    mic.begin();
    epd.begin();

    // 4. Start Wi-Fi
    wifi_init();

    // 5. Wait for Wi-Fi connection
    xEventGroupWaitBits(app_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

    // 6. Initialize MicroLink Tailscale Client
    microlink_config_t ml_config = {};
    ml_config.auth_key = TAILSCALE_AUTH_KEY;
    ml_config.device_name = TAILSCALE_DEVICE_NAME;
    ml_config.enable_derp = true;
    ml_config.enable_stun = true;
    ml_config.enable_disco = true;
    ml_config.max_peers = 16;
    ml_config.wifi_tx_power_dbm = 13;

    ESP_LOGI(TAG, "Initializing MicroLink with Auth Key: %.15s...", TAILSCALE_AUTH_KEY);
    ml = microlink_init(&ml_config);
    if (!ml) {
        ESP_LOGE(TAG, "Failed to initialize MicroLink Tailscale client!");
        return;
    }

    microlink_set_state_callback(ml, on_tailscale_state_change, NULL);
    microlink_set_peer_callback(ml, on_tailscale_peer_update, NULL);

    ESP_LOGI(TAG, "Starting Tailscale connection & node registration...");
    ESP_ERROR_CHECK(microlink_start(ml));

    // 7. Spawn audio capture worker on Core 0
    xTaskCreatePinnedToCore(
        audio_streaming_task,
        "AudioStreamer",
        8192,
        NULL,
        2,
        NULL,
        0 // Core 0
    );

    // 8. Main event loop on Core 1: wait for Tailscale then manage refreshes
    xEventGroupWaitBits(app_event_group, TAILSCALE_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
    uint32_t comack-server_ip = 0;
    while (!(comack-server_ip = resolve_server())) {
        ESP_LOGW(TAG, "Resolving '%s' via Tailscale MagicDNS...", COMACK_SERVER_HOST);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    char resolved_ip_str[16];
    microlink_ip_to_str(comack-server_ip, resolved_ip_str);
    ESP_LOGI(TAG, "MagicDNS resolved '%s' -> %s", COMACK_SERVER_HOST, resolved_ip_str);

    gpio_config_t btn = {};
    btn.pin_bit_mask = (1ULL << PIN_BTN_FWD) | (1ULL << PIN_BTN_BACK) | (1ULL << PIN_BTN_TRIGGER);
    btn.mode = GPIO_MODE_INPUT;
    btn.pull_up_en = GPIO_PULLUP_ENABLE;
    gpio_config(&btn);

    // Poll for a newly revealed comic; the server only bumps "current" after the silence gate opens.
    // ponytail: 10s polling, not a /ws/events subscription. Add the socket if latency matters.
    // Buttons browse older issues; after BROWSE_TIMEOUT_MS idle we snap back to the latest.
    int latest = -1, shown = -1;
    int64_t next_poll_us = 0, browse_until_us = 0;
    bool fwd_was = false, back_was = false, trig_was = false;
    while (true) {
        int64_t now = esp_timer_get_time();

        if (now >= next_poll_us) {
            next_poll_us = now + 10000 * 1000LL;
            uint8_t buf[256];
            int n = http_request(comack-server_ip, "GET", "/api/comics/current", buf, sizeof(buf) - 1);
            if (n > 0) {
                buf[n] = 0;
                const char *p = strstr((const char *)buf, "\"issue_number\":");
                if (p) latest = atoi(p + strlen("\"issue_number\":"));
            }
            // Snap back to (or advance to) the latest once browsing has timed out.
            bool browsing = now < browse_until_us;
            if (!browsing && latest > 0 && latest != shown && download_and_refresh_comic(comack-server_ip, latest)) {
                shown = latest;
            }
        }

        // Edge-triggered presses; the 50ms tick debounces.
        bool fwd = gpio_get_level((gpio_num_t)PIN_BTN_FWD) == 0;
        bool back = gpio_get_level((gpio_num_t)PIN_BTN_BACK) == 0;
        int step = (fwd && !fwd_was) ? 1 : (back && !back_was) ? -1 : 0;
        fwd_was = fwd;
        back_was = back;

        bool trig = gpio_get_level((gpio_num_t)PIN_BTN_TRIGGER) == 0;
        if (trig && !trig_was) {
            ESP_LOGI(TAG, "Button: trigger");
            uint8_t ignored[64];
            http_request(comack-server_ip, "POST", "/api/trigger", ignored, sizeof(ignored));
            browse_until_us = 0;                            // stop browsing, show the new comic
            next_poll_us = esp_timer_get_time() + 2000 * 1000LL;  // a held comic is revealed at once
        }
        trig_was = trig;

        if (step && shown > 0) {
            browse_until_us = esp_timer_get_time() + BROWSE_TIMEOUT_MS * 1000LL;
            // Walk past deleted issues; never beyond the latest revealed one.
            // ponytail: a dropped download also counts as a gap and gets skipped.
            for (int target = shown + step; target >= 1 && target <= latest; target += step) {
                ESP_LOGI(TAG, "Button: %s to #%d", step > 0 ? "forward" : "back", target);
                if (download_and_refresh_comic(comack-server_ip, target)) {
                    shown = target;
                    break;
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(50));
    }
}
