idf_component_register(
    SRCS "main.cpp" "mic_sampler.cpp" "epd_display.cpp" "ws_client.cpp"
    INCLUDE_DIRS "." "../include"
    REQUIRES esp_wifi nvs_flash esp_netif esp_event esp_timer lwip driver esp_driver_i2s esp_driver_spi esp_driver_gpio microlink
)
