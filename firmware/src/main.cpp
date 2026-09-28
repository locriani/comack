#include <stdio.h>
#include <string.h>
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

#include "config.h"
#include "mic_sampler.h"
#include "epd_display.h"
#include "microlink.h"

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

static void download_and_refresh_comic(uint32_t dest_ip) {
    ESP_LOGI(TAG, "Fetching comic binary from comack-server (100.64.0.1:8000)...");
    microlink_tcp_socket_t *sock = microlink_tcp_connect(ml, dest_ip, COMACK_SERVER_PORT, 10000);
    if (!sock) {
        ESP_LOGE(TAG, "Failed to connect TCP to comack-server over Tailscale!");
        return;
    }

    // Send HTTP GET request
    const char *get_req = "GET /api/comics/current.bin HTTP/1.1\r\n"
                          "Host: 100.64.0.1:8000\r\n"
                          "Connection: close\r\n\r\n";
    microlink_tcp_send(sock, get_req, strlen(get_req));

    // Read HTTP response headers then raw 48,000 bytes
    char header_buf[1024];
    size_t total_body = 0;
    bool in_body = false;

    while (total_body < EPD_BUFFER_BYTES) {
        uint8_t chunk[2048];
        int r = microlink_tcp_recv(sock, chunk, sizeof(chunk), 5000);
        if (r <= 0) break;

        if (!in_body) {
            // Find \r\n\r\n separator
            for (int i = 0; i < r - 3; i++) {
                if (chunk[i] == '\r' && chunk[i+1] == '\n' && chunk[i+2] == '\r' && chunk[i+3] == '\n') {
                    in_body = true;
                    int body_start = i + 4;
                    int body_bytes = r - body_start;
                    if (body_bytes > 0) {
                        memcpy(epdFrameBuffer, chunk + body_start, body_bytes);
                        total_body += body_bytes;
                    }
                    break;
                }
            }
        } else {
            size_t to_copy = (total_body + r > EPD_BUFFER_BYTES) ? (EPD_BUFFER_BYTES - total_body) : r;
            memcpy(epdFrameBuffer + total_body, chunk, to_copy);
            total_body += to_copy;
        }
    }

    microlink_tcp_close(sock);

    if (total_body == EPD_BUFFER_BYTES) {
        ESP_LOGI(TAG, "Complete comic binary received (%d bytes). Updating e-Paper!", (int)total_body);
        epd.displayFrame(epdFrameBuffer);
    } else {
        ESP_LOGW(TAG, "Incomplete comic binary received: %d / %d bytes", (int)total_body, EPD_BUFFER_BYTES);
    }
}

static void audio_streaming_task(void *pvParameters) {
    uint32_t dest_ip = microlink_parse_ip(COMACK_SERVER_HOST);
    ESP_LOGI(TAG, "Audio streaming task running on Core 0.");

    while (true) {
        xEventGroupWaitBits(app_event_group, TAILSCALE_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

        // Sample audio from onboard PDM microphone
        size_t samples = mic.readSamples(audioChunkBuffer, AUDIO_CHUNK_SAMPLES);
        if (samples > 0) {
            // Audio streaming over Tailscale TCP socket
            microlink_tcp_socket_t *audio_sock = microlink_tcp_connect(ml, dest_ip, COMACK_SERVER_PORT, 2000);
            if (audio_sock) {
                microlink_tcp_send(audio_sock, audioChunkBuffer, samples * sizeof(int16_t));
                microlink_tcp_close(audio_sock);
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
    uint32_t comack-server_ip = microlink_parse_ip(COMACK_SERVER_HOST);

    // Initial comic display load
    download_and_refresh_comic(comack-server_ip);

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
