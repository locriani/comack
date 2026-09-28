#pragma once
#include <stdint.h>
#include <stddef.h>
#include <driver/i2s_pdm.h>
#include "config.h"

class MicSampler {
public:
    MicSampler();
    bool begin();
    size_t readSamples(int16_t* buffer, size_t num_samples);
    void end();

private:
    bool _initialized;
    i2s_chan_handle_t _rx_chan;
};
