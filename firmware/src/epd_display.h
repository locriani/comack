#pragma once
#include <Arduino.h>
#include <SPI.h>
#include "config.h"

class EPDDisplay {
public:
    EPDDisplay();
    bool begin();
    void reset();
    void waitBusy();
    void displayFrame(const uint8_t* image_buffer);
    void sleep();

private:
    void sendCommand(uint8_t command);
    void sendData(uint8_t data);
    void sendDataBytes(const uint8_t* data, size_t length);
};
