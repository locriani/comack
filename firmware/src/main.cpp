#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

#include "config.h"
#include "mic_sampler.h"
#include "epd_display.h"

// Hardware drivers
MicSampler mic;
EPDDisplay epd;

// WebSockets clients
WebSocketsClient wsAudio;
WebSocketsClient wsEvents;

// PSRAM Buffers
int16_t* audioChunkBuffer = nullptr;
uint8_t* epdFrameBuffer = nullptr;

// Flags & State
volatile bool newComicAvailable = false;
unsigned long lastHeartbeat = 0;

void downloadAndDisplayComic() {
    Serial.println("[MAIN] Fetching new comic binary from comack-server...");
    HTTPClient http;
    String url = String("http://") + COMACK_SERVER_HOST + ":" + COMACK_SERVER_PORT + "/api/comics/current.bin";

    http.begin(url);
    int httpCode = http.GET();

    if (httpCode == HTTP_CODE_OK) {
        WiFiClient* stream = http.getStreamPtr();
        size_t totalRead = 0;

        while (http.connected() && (totalRead < EPD_BUFFER_BYTES)) {
            size_t available = stream->available();
            if (available) {
                size_t readNow = stream->readBytes((char*)(epdFrameBuffer + totalRead), 
                                                   min(available, EPD_BUFFER_BYTES - totalRead));
                totalRead += readNow;
            }
            delay(1);
        }

        if (totalRead == EPD_BUFFER_BYTES) {
            Serial.println("[MAIN] Comic binary loaded. Executing sneaky e-paper refresh!");
            epd.displayFrame(epdFrameBuffer);
        } else {
            Serial.printf("[MAIN] Error: Incomplete read (%d / %d bytes)\n", totalRead, EPD_BUFFER_BYTES);
        }
    } else {
        Serial.printf("[MAIN] HTTP GET failed: %d\n", httpCode);
    }
    http.end();
}

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
        case WStype_DISCONNECTED:
            Serial.println("[WS-EVENTS] Disconnected from server.");
            break;
        case WStype_CONNECTED:
            Serial.println("[WS-EVENTS] Connected to server event bus.");
            break;
        case WStype_TEXT: {
            JsonDocument doc;
            DeserializationError error = deserializeJson(doc, payload, length);
            if (!error) {
                const char* eventType = doc["event"];
                if (eventType && strcmp(eventType, "SNEAKY_COMIC_REFRESH") == 0) {
                    Serial.println("[WS-EVENTS] *** SNEAKY REFRESH COMMAND RECEIVED ***");
                    newComicAvailable = true;
                }
            }
            break;
        }
        default:
            break;
    }
}

void audioStreamingTask(void* parameter) {
    Serial.println("[TASK] Audio streaming worker started on Core 0.");
    while (true) {
        if (WiFi.status() == WL_CONNECTED && wsAudio.isConnected()) {
            size_t samplesRead = mic.readSamples(audioChunkBuffer, AUDIO_CHUNK_SAMPLES);
            if (samplesRead > 0) {
                wsAudio.sendBIN((uint8_t*)audioChunkBuffer, samplesRead * sizeof(int16_t));
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n==========================================");
    Serial.println("  COMACK — reTerminal E1001 Display App   ");
    Serial.println("==========================================");

    // 1. Allocate buffers in PSRAM
    audioChunkBuffer = (int16_t*)ps_malloc(AUDIO_CHUNK_SAMPLES * sizeof(int16_t));
    epdFrameBuffer = (uint8_t*)ps_malloc(EPD_BUFFER_BYTES);

    if (!audioChunkBuffer || !epdFrameBuffer) {
        Serial.println("[MAIN] Error: Failed to allocate PSRAM buffers!");
    } else {
        Serial.println("[MAIN] PSRAM buffers allocated successfully.");
    }

    // 2. Initialize hardware peripherals
    mic.begin();
    epd.begin();

    // 3. Connect Wi-Fi
    Serial.printf("[MAIN] Connecting to Wi-Fi: %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n[MAIN] Wi-Fi connected! IP: " + WiFi.localIP().toString());
    } else {
        Serial.println("\n[MAIN] Wi-Fi connection timed out. Will retry in loop.");
    }

    // 4. Setup WebSockets
    wsAudio.begin(COMACK_SERVER_HOST, COMACK_SERVER_PORT, "/ws/audio");
    wsAudio.setReconnectInterval(2500);

    wsEvents.begin(COMACK_SERVER_HOST, COMACK_SERVER_PORT, "/ws/events");
    wsEvents.onEvent(onWsEvent);
    wsEvents.setReconnectInterval(2500);

    // 5. Spawn audio capture worker on Core 0
    xTaskCreatePinnedToCore(
        audioStreamingTask,
        "AudioStreamer",
        8192,
        NULL,
        2, // Priority
        NULL,
        0  // Core 0
    );

    // 6. Perform initial screen load
    newComicAvailable = true;
}

void loop() {
    wsAudio.loop();
    wsEvents.loop();

    // Heartbeat to keep events WebSocket active
    if (millis() - lastHeartbeat > 30000) {
        lastHeartbeat = millis();
        if (wsEvents.isConnected()) {
            wsEvents.sendTXT("{\"cmd\":\"PING\"}");
        }
    }

    // Handle stealth comic refresh
    if (newComicAvailable) {
        newComicAvailable = false;
        downloadAndDisplayComic();
    }

    delay(5);
}
