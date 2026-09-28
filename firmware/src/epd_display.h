#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <driver/spi_master.h>
#include <driver/gpio.h>
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
    spi_device_handle_t _spi;
    void sendCommand(uint8_t command);
    void sendData(uint8_t data);
    void sendDataBytes(const uint8_t* data, size_t length);
};
