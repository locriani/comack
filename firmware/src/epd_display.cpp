#include "epd_display.h"
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

static const char* TAG = "EPD";

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

EPDDisplay::EPDDisplay() : _spi(NULL) {}

bool EPDDisplay::begin() {
    // 1. Configure control GPIOs
    gpio_config_t io_conf = {};
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_OUTPUT;
    io_conf.pin_bit_mask = (1ULL << PIN_EPD_DC) | (1ULL << PIN_EPD_RST);
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_conf.pull_up_en = GPIO_PULLUP_DISABLE;
    gpio_config(&io_conf);

    // Busy pin is input with pullup
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = (1ULL << PIN_EPD_BUSY);
    io_conf.pull_up_en = GPIO_PULLUP_ENABLE;
    gpio_config(&io_conf);

    // 2. Initialize SPI bus (SPI2_HOST)
    spi_bus_config_t buscfg = {};
    buscfg.mosi_io_num = PIN_EPD_MOSI;
    buscfg.miso_io_num = -1;
    buscfg.sclk_io_num = PIN_EPD_SCK;
    buscfg.quadwp_io_num = -1;
    buscfg.quadhd_io_num = -1;
    buscfg.max_transfer_sz = EPD_BUFFER_BYTES + 32;

    esp_err_t ret = spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize SPI bus: %d", ret);
        return false;
    }

    spi_device_interface_config_t devcfg = {};
    devcfg.clock_speed_hz = 10 * 1000 * 1000; // 10 MHz
    devcfg.mode = 0;                            // SPI mode 0
    devcfg.spics_io_num = PIN_EPD_CS;           // Hardware CS
    devcfg.queue_size = 1;

    ret = spi_bus_add_device(SPI2_HOST, &devcfg, &_spi);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to add SPI device: %d", ret);
        return false;
    }

    ESP_LOGI(TAG, "7.5\" e-Paper SPI interface initialized.");
    return true;
}

void EPDDisplay::reset() {
    gpio_set_level((gpio_num_t)PIN_EPD_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level((gpio_num_t)PIN_EPD_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(4));
    gpio_set_level((gpio_num_t)PIN_EPD_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(20));
}

void EPDDisplay::waitBusy() {
    // BUSY pin on reTerminal E1001 is active LOW (0 = busy, 1 = idle)
    int64_t start = esp_timer_get_time();
    while (gpio_get_level((gpio_num_t)PIN_EPD_BUSY) == 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
        if ((esp_timer_get_time() - start) > 20000000) { // 20s timeout
            ESP_LOGW(TAG, "Wait busy timeout!");
            break;
        }
    }
}

void EPDDisplay::sendCommand(uint8_t command) {
    gpio_set_level((gpio_num_t)PIN_EPD_DC, 0);
    spi_transaction_t t = {};
    t.length = 8;
    t.tx_buffer = &command;
    spi_device_polling_transmit(_spi, &t);
}

void EPDDisplay::sendData(uint8_t data) {
    gpio_set_level((gpio_num_t)PIN_EPD_DC, 1);
    spi_transaction_t t = {};
    t.length = 8;
    t.tx_buffer = &data;
    spi_device_polling_transmit(_spi, &t);
}

void EPDDisplay::sendDataBytes(const uint8_t* data, size_t length) {
    gpio_set_level((gpio_num_t)PIN_EPD_DC, 1);
    // One SPI transaction is limited to 32 KB on the S3, so send in chunks.
    // Frames arrive as 1 = white, but with this init (CDI 0x10) register 0x13 treats 1 = black: invert.
    const size_t CHUNK = 4096;
    static uint8_t chunk[CHUNK];
    for (size_t off = 0; off < length; off += CHUNK) {
        size_t n = (length - off < CHUNK) ? (length - off) : CHUNK;
        for (size_t i = 0; i < n; i++) chunk[i] = ~data[off + i];
        spi_transaction_t t = {};
        t.length = n * 8;
        t.tx_buffer = chunk;
        esp_err_t err = spi_device_polling_transmit(_spi, &t);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "SPI transmit failed at offset %d: %d", (int)off, err);
            return;
        }
    }
}

void EPDDisplay::displayFrame(const uint8_t* image_buffer) {
    ESP_LOGI(TAG, "Starting e-Paper refresh cycle...");
    reset();
    waitBusy();

    // Power setting: VGH/VGL 20V, VDH/VDL 15V
    sendCommand(EPD_CMD_POWER_SETTING);
    sendData(0x07);
    sendData(0x07);
    sendData(0x3F);
    sendData(0x3F);

    // Power on
    sendCommand(EPD_CMD_POWER_ON);
    vTaskDelay(pdMS_TO_TICKS(100));
    waitBusy();

    // Panel setting (KW mode, LUT from OTP)
    sendCommand(EPD_CMD_PANEL_SETTING);
    sendData(0x1F);

    // Resolution setting (800x480)
    sendCommand(EPD_CMD_RESOLUTION_SETTING);
    sendData(EPD_WIDTH >> 8);
    sendData(EPD_WIDTH & 0xFF);
    sendData(EPD_HEIGHT >> 8);
    sendData(EPD_HEIGHT & 0xFF);

    sendCommand(0x15);
    sendData(0x00);

    // VCOM and data interval
    sendCommand(0x50);
    sendData(0x10);
    sendData(0x07);

    // TCON setting
    sendCommand(EPD_CMD_TCON_SETTING);
    sendData(0x22);

    // Transfer bitmap data (1 = white, 0 = black; 1-bit mode uses the new-data register only)
    sendCommand(EPD_CMD_DATA_START_2);
    sendDataBytes(image_buffer, EPD_BUFFER_BYTES);

    // Trigger display refresh
    sendCommand(EPD_CMD_DISPLAY_REFRESH);
    vTaskDelay(pdMS_TO_TICKS(100));
    waitBusy();

    ESP_LOGI(TAG, "Display refreshed successfully.");
    sleep();
}

void EPDDisplay::sleep() {
    sendCommand(EPD_CMD_POWER_OFF);
    waitBusy();
    sendCommand(EPD_CMD_DEEP_SLEEP);
    sendData(0xA5); // Deep sleep check code
}
