#include "mic_sampler.h"
#include <driver/gpio.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

static const char* TAG = "MIC";

MicSampler::MicSampler() : _initialized(false), _rx_chan(NULL) {}

bool MicSampler::begin() {
    // 1. Enable power to microphone gate
    gpio_set_direction((gpio_num_t)PIN_MIC_PWR, GPIO_MODE_OUTPUT);
    gpio_set_level((gpio_num_t)PIN_MIC_PWR, 1);
    vTaskDelay(pdMS_TO_TICKS(50)); // Power stabilization delay

    // 2. Allocate I2S channel in Master RX mode
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to allocate I2S channel: %d", err);
        return false;
    }

    // 3. Configure I2S PDM RX mode
    i2s_pdm_rx_gpio_config_t gpio_cfg = {
        .clk = (gpio_num_t)PIN_MIC_CLK,
        .din = (gpio_num_t)PIN_MIC_DATA,
        .invert_flags = { .clk_inv = false },
    };

    i2s_pdm_rx_config_t pdm_rx_cfg = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = gpio_cfg,
    };

    err = i2s_channel_init_pdm_rx_mode(_rx_chan, &pdm_rx_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize PDM RX mode: %d", err);
        i2s_del_channel(_rx_chan);
        _rx_chan = NULL;
        return false;
    }

    err = i2s_channel_enable(_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable I2S channel: %d", err);
        i2s_del_channel(_rx_chan);
        _rx_chan = NULL;
        return false;
    }

    _initialized = true;
    ESP_LOGI(TAG, "PDM Microphone initialized successfully at 16kHz.");
    return true;
}

size_t MicSampler::readSamples(int16_t* buffer, size_t num_samples) {
    if (!_initialized || !_rx_chan) return 0;
    size_t bytes_to_read = num_samples * sizeof(int16_t);
    size_t bytes_read = 0;
    
    esp_err_t err = i2s_channel_read(_rx_chan, buffer, bytes_to_read, &bytes_read, portMAX_DELAY);
    if (err != ESP_OK) {
        return 0;
    }
    return bytes_read / sizeof(int16_t);
}

void MicSampler::end() {
    if (_initialized && _rx_chan) {
        i2s_channel_disable(_rx_chan);
        i2s_del_channel(_rx_chan);
        _rx_chan = NULL;
        gpio_set_level((gpio_num_t)PIN_MIC_PWR, 0);
        _initialized = false;
    }
}
