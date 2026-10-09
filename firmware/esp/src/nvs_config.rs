//! Persisting the device configuration on the chip: an NVS adapter for the
//! core's [`DeviceConfigStore`] port.
//!
//! Everything lives under the `comack` namespace with a single blob key, so
//! the provisioning portal (next task) can write a configuration and boot
//! can read it back without knowing NVS details.

use anyhow::{Context, Result};
use comack_core::application::ports::DeviceConfigStore;
use comack_core::domain::{DeviceConfig, ENCODED_MAX};
use esp_idf_svc::nvs::{EspDefaultNvsPartition, EspNvs, NvsDefault};
use log::info;

/// The NVS namespace the device configuration lives in.
const NAMESPACE: &str = "comack";

/// The NVS key holding the encoded [`DeviceConfig`].
const CONFIG_KEY: &str = "device_config";

/// Reads and writes the device configuration in the default NVS partition.
pub struct NvsDeviceConfigStore {
    nvs: EspNvs<NvsDefault>,
}

impl NvsDeviceConfigStore {
    /// Opens the store read-write.
    pub fn open(partition: EspDefaultNvsPartition) -> Result<Self> {
        let nvs =
            EspNvs::new(partition, NAMESPACE, true).context("opening the comack NVS namespace")?;
        Ok(Self { nvs })
    }
}

impl DeviceConfigStore for NvsDeviceConfigStore {
    type Error = anyhow::Error;

    fn load(&self) -> Result<Option<DeviceConfig>, Self::Error> {
        // ENCODED_MAX holds any configuration this firmware writes; a
        // larger blob (foreign or hand-planted) errors out rather than
        // truncating silently.
        let mut buffer = [0u8; ENCODED_MAX];
        let Some(bytes) = self.nvs.get_blob(CONFIG_KEY, &mut buffer)? else {
            return Ok(None);
        };
        DeviceConfig::decode(bytes)
            .map(Some)
            .context("decoding the stored device configuration")
    }

    fn save(&mut self, config: &DeviceConfig) -> Result<(), Self::Error> {
        let encoded = config.encode();
        self.nvs
            .set_blob(CONFIG_KEY, &encoded)
            .context("saving the device configuration")?;
        info!("Device configuration saved to NVS.");
        Ok(())
    }

    fn clear(&mut self) -> Result<(), Self::Error> {
        self.nvs
            .remove(CONFIG_KEY)
            .context("clearing the device configuration")?;
        info!("Device configuration cleared from NVS.");
        Ok(())
    }
}
