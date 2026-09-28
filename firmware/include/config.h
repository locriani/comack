#pragma once
#include <Arduino.h>

// ==========================================
// Network & Comack Server Configuration
// ==========================================
#define WIFI_SSID           "YOUR_WIFI_SSID"
#define WIFI_PASSWORD       "YOUR_WIFI_PASSWORD"

// Deskserver Host Configuration
// If on local LAN: use hostname "comack-server.local" or IP e.g. "192.168.1.100"
#define COMACK_SERVER_HOST  "comack-server.local"
#define COMACK_SERVER_PORT  8000

// Audio Streaming Sample Rate
#define AUDIO_SAMPLE_RATE   16000
#define AUDIO_CHUNK_SAMPLES 1600 // 100ms at 16kHz = 1600 samples (3200 bytes)

// ==========================================
// reTerminal E1001 Hardware Pin Definitions
// ==========================================

// PDM Microphone (ESP32-S3 I2S0)
#define PIN_MIC_PWR         38  // Active HIGH to power microphone
#define PIN_MIC_DATA        41  // PDM DIN
#define PIN_MIC_CLK         42  // PDM CLK

// 7.5" e-Paper Display (SPI)
#define PIN_EPD_SCK         7   // SPI CLK
#define PIN_EPD_MOSI        9   // SPI MOSI
#define PIN_EPD_CS          10  // Chip Select
#define PIN_EPD_DC          11  // Data / Command
#define PIN_EPD_RST         12  // Reset
#define PIN_EPD_BUSY        13  // Busy signal (Active LOW / inverted)

// Power & Battery Measurement
#define PIN_BATTERY_EN      21  // Enable battery divider
#define PIN_BATTERY_ADC     1   // Battery voltage ADC

// Display Specifications
#define EPD_WIDTH           800
#define EPD_HEIGHT          480
#define EPD_BUFFER_BYTES    (EPD_WIDTH * EPD_HEIGHT / 8) // Exactly 48,000 bytes
