#include "epd_display.h"

// Waveshare 7.50inv2 EPD Command Codes
#define EPD_CMD_PANEL_SETTING       0x00
#define EPD_CMD_POWER_SETTING       0x01
#define EPD_CMD_POWER_OFF           0x02
#define EPD_CMD_POWER_ON            0x04
#define EPD_CMD_BOOSTER_SOFT_START  0x06
#define EPD_CMD_DEEP_SLEEP          0x07
#define EPD_CMD_DATA_START_1        0x10
#define EPD_CMD_DISPLAY_REFRESH     0x12
#define EPD_CMD_DATA_START_2        0x13
#define EPD_CMD_TCON_SETTING        0x60
#define EPD_CMD_RESOLUTION_SETTING  0x61

EPDDisplay::EPDDisplay() {}

bool EPDDisplay::begin() {
    pinMode(PIN_EPD_CS, OUTPUT);
    pinMode(PIN_EPD_DC, OUTPUT);
    pinMode(PIN_EPD_RST, OUTPUT);
    pinMode(PIN_EPD_BUSY, INPUT);

    digitalWrite(PIN_EPD_CS, HIGH);

    // Initialize SPI on custom pins
    SPI.begin(PIN_EPD_SCK, -1, PIN_EPD_MOSI, PIN_EPD_CS);
    SPI.beginTransaction(SPISettings(10000000, MSBFIRST, SPI_MODE0));

    Serial.println("[EPD] Initialized SPI interface.");
    return true;
}

void EPDDisplay::reset() {
    digitalWrite(PIN_EPD_RST, HIGH);
    delay(20);
    digitalWrite(PIN_EPD_RST, LOW);
    delay(4);
    digitalWrite(PIN_EPD_RST, HIGH);
    delay(20);
}

void EPDDisplay::waitBusy() {
    // BUSY pin on reTerminal E1001 is active LOW (0 = busy, 1 = idle)
    unsigned long start = millis();
    while (digitalRead(PIN_EPD_BUSY) == LOW) {
        delay(10);
        if (millis() - start > 20000) {
            Serial.println("[EPD] Warning: Wait busy timeout!");
            break;
        }
    }
}

void EPDDisplay::sendCommand(uint8_t command) {
    digitalWrite(PIN_EPD_DC, LOW);
    digitalWrite(PIN_EPD_CS, LOW);
    SPI.transfer(command);
    digitalWrite(PIN_EPD_CS, HIGH);
}

void EPDDisplay::sendData(uint8_t data) {
    digitalWrite(PIN_EPD_DC, HIGH);
    digitalWrite(PIN_EPD_CS, LOW);
    SPI.transfer(data);
    digitalWrite(PIN_EPD_CS, HIGH);
}

void EPDDisplay::sendDataBytes(const uint8_t* data, size_t length) {
    digitalWrite(PIN_EPD_DC, HIGH);
    digitalWrite(PIN_EPD_CS, LOW);
    SPI.transferBytes(data, NULL, length);
    digitalWrite(PIN_EPD_CS, HIGH);
}

void EPDDisplay::displayFrame(const uint8_t* image_buffer) {
    Serial.println("[EPD] Starting display refresh cycle...");
    reset();
    waitBusy();

    // Booster soft start
    sendCommand(EPD_CMD_BOOSTER_SOFT_START);
    sendData(0x17);
    sendData(0x17);
    sendData(0x28);
    sendData(0x17);

    // Power on
    sendCommand(EPD_CMD_POWER_ON);
    waitBusy();

    // Panel setting (800x480, OTP LUT)
    sendCommand(EPD_CMD_PANEL_SETTING);
    sendData(0x1F); // 800x480, LUT from OTP

    // Resolution setting
    sendCommand(EPD_CMD_RESOLUTION_SETTING);
    sendData(EPD_WIDTH >> 8);
    sendData(EPD_WIDTH & 0xFF);
    sendData(EPD_HEIGHT >> 8);
    sendData(EPD_HEIGHT & 0xFF);

    // Transfer bitmap data
    sendCommand(EPD_CMD_DATA_START_1);
    sendDataBytes(image_buffer, EPD_BUFFER_BYTES);

    sendCommand(EPD_CMD_DATA_START_2);
    sendDataBytes(image_buffer, EPD_BUFFER_BYTES);

    // Trigger display refresh
    sendCommand(EPD_CMD_DISPLAY_REFRESH);
    delay(100);
    waitBusy();

    Serial.println("[EPD] Display refreshed successfully.");
    sleep();
}

void EPDDisplay::sleep() {
    sendCommand(EPD_CMD_POWER_OFF);
    waitBusy();
    sendCommand(EPD_CMD_DEEP_SLEEP);
    sendData(0xA5); // Deep sleep check code
}
