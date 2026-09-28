#pragma once
#include <Arduino.h>
#include "config.h"

class MicSampler {
public:
    MicSampler();
    bool begin();
    size_t readSamples(int16_t* buffer, size_t num_samples);
    void end();

private:
    bool _initialized;
};
