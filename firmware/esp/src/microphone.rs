//! [`AudioSource`] for the onboard PDM microphone on I2S0.

use comack_core::application::ports::AudioSource;
use esp_idf_svc::hal::delay::{FreeRtos, BLOCK};
use esp_idf_svc::hal::gpio::{InputPin, Output, OutputPin, PinDriver};
use esp_idf_svc::hal::i2s::config::{
    Config, DataBitWidth, PdmRxClkConfig, PdmRxConfig, PdmRxGpioConfig, PdmRxSlotConfig, SlotMode,
};
use esp_idf_svc::hal::i2s::{I2s, I2sDriver, I2sRx};
use esp_idf_svc::sys::EspError;
use log::info;

/// 16-bit mono PCM from the PDM microphone. Reads block until the DMA
/// buffers fill, which paces the audio loop.
pub struct PdmMicrophone {
    driver: I2sDriver<'static, I2sRx>,
    /// Held high to keep the microphone powered.
    _power: PinDriver<'static, Output>,
    bytes: Vec<u8>,
}

impl PdmMicrophone {
    /// Power the microphone through `power` (active high) and start PDM
    /// capture at `sample_rate_hz`, reading up to `max_samples` per call.
    pub fn new(
        i2s: impl I2s + 'static,
        clk: impl OutputPin + 'static,
        din: impl InputPin + 'static,
        power: impl OutputPin + 'static,
        sample_rate_hz: u32,
        max_samples: usize,
    ) -> Result<Self, EspError> {
        let mut power = PinDriver::output(power)?;
        power.set_high()?;
        FreeRtos::delay_ms(50); // supply settling

        let config = PdmRxConfig::new(
            Config::default(),
            PdmRxClkConfig::from_sample_rate_hz(sample_rate_hz),
            PdmRxSlotConfig::from_bits_per_sample_and_slot_mode(
                DataBitWidth::Bits16,
                SlotMode::Mono,
            ),
            PdmRxGpioConfig::new(false),
        );
        let mut driver = I2sDriver::new_pdm_rx(i2s, &config, clk, din)?;
        driver.rx_enable()?;
        info!("PDM microphone initialized at {sample_rate_hz} Hz.");
        Ok(Self {
            driver,
            _power: power,
            bytes: vec![0; max_samples * 2],
        })
    }
}

impl AudioSource for PdmMicrophone {
    type Error = EspError;

    fn read(&mut self, buffer: &mut [i16]) -> Result<usize, EspError> {
        let want = (buffer.len() * 2).min(self.bytes.len());
        let got = self.driver.read(&mut self.bytes[..want], BLOCK)?;
        let samples = got / 2;
        for (sample, bytes) in buffer
            .iter_mut()
            .zip(self.bytes[..samples * 2].chunks_exact(2))
        {
            *sample = i16::from_le_bytes([bytes[0], bytes[1]]);
        }
        Ok(samples)
    }
}
