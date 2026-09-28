#include "mic_sampler.h"
#include <driver/i2s.h>
#include <driver/gpio.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

static const char* TAG = "MIC";
#define I2S_PORT I2S_NUM_0

MicSampler::MicSampler() : _initialized(false) {}

bool MicSampler::begin() {
    // 1. Enable power to microphone gate
    gpio_set_direction((gpio_num_t)PIN_MIC_PWR, GPIO_MODE_OUTPUT);
    gpio_set_level((gpio_num_t)PIN_MIC_PWR, 1);
    vTaskDelay(pdMS_TO_TICKS(50)); // Power stabilization delay

    // 2. Configure I2S in PDM RX mode
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
        .sample_rate = AUDIO_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 512,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_PIN_NO_CHANGE,
        .ws_io_num = PIN_MIC_CLK,      // GPIO 42
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = PIN_MIC_DATA     // GPIO 41
    };

    esp_err_t err = i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to install I2S driver: %d", err);
        return false;
    }

    err = i2s_set_pin(I2S_PORT, &pin_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set I2S pins: %d", err);
        i2s_driver_uninstall(I2S_PORT);
        return false;
    }

    _initialized = true;
    ESP_LOGI(TAG, "PDM Microphone initialized successfully at 16kHz.");
    return true;
}

size_t MicSampler::readSamples(int16_t* buffer, size_t num_samples) {
    if (!_initialized) return 0;
    size_t bytes_to_read = num_samples * sizeof(int16_t);
    size_t bytes_read = 0;
    
    esp_err_t err = i2s_read(I2S_PORT, (char*)buffer, bytes_to_read, &bytes_read, portMAX_DELAY);
    if (err != ESP_OK) {
        return 0;
    }
    return bytes_read / sizeof(int16_t);
}

void MicSampler::end() {
    if (_initialized) {
        i2s_driver_uninstall(I2S_PORT);
        gpio_set_level((gpio_num_t)PIN_MIC_PWR, 0);
        _initialized = false;
    }
}
