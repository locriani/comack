//! Getting the configuration the device runs with.
//!
//! [`load_or_fallback`] is the whole policy: what the config store holds
//! wins; when nothing is stored, or the store fails, the compile-time
//! fallback keeps the device booting exactly as it did before runtime
//! configuration existed.

use crate::application::ports::DeviceConfigStore;
use crate::domain::DeviceConfig;

/// Which configuration won: the store's, or the compile-time fallback.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConfigSource {
    /// The store held a decodable configuration.
    Stored,
    /// The store was empty (or could not be read by a caller that chose to
    /// fall through), so the compile-time fallback applies.
    Fallback,
}

/// Loads the stored configuration, reporting where it came from.
///
/// Store errors and unreadable data are surfaced to the caller (who logs
/// them) rather than silently swallowed here; the fallback answers both
/// only when the caller decides so.
pub fn load_with_source<S: DeviceConfigStore>(
    store: &S,
    fallback: &DeviceConfig,
) -> Result<(DeviceConfig, ConfigSource), S::Error> {
    match store.load()? {
        Some(config) => Ok((config, ConfigSource::Stored)),
        None => Ok((fallback.clone(), ConfigSource::Fallback)),
    }
}

/// Loads the stored configuration, or falls back when there is none.
///
/// Store errors and unreadable data are surfaced to the caller (who logs
/// them) rather than silently swallowed here; the fallback answers both.
pub fn load_or_fallback<S: DeviceConfigStore>(
    store: &S,
    fallback: &DeviceConfig,
) -> Result<DeviceConfig, S::Error> {
    load_with_source(store, fallback).map(|(config, _)| config)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::testing::InMemoryConfigStore;
    use crate::config::ServerEndpoint;
    use crate::domain::WifiCredentials;

    /// What the ESP composition root derives from its compile-time
    /// `secrets.rs` and `config.rs` constants.
    fn fallback() -> DeviceConfig {
        let networks = heapless::Vec::from([WifiCredentials::new("home", "pw").unwrap()]);
        DeviceConfig::from_compile_time(
            &networks,
            ServerEndpoint {
                host: "localhost",
                port: 8000,
            },
            "comack-display",
            "",
            "",
            "tskey-auth-fallback",
        )
        .unwrap()
    }

    fn stored_config() -> DeviceConfig {
        let networks =
            heapless::Vec::from([WifiCredentials::new("phone-hotspot", "secret").unwrap()]);
        DeviceConfig::from_compile_time(
            &networks,
            ServerEndpoint {
                host: "other-server",
                port: 9000,
            },
            "kitchen-display",
            "kitchen-display",
            "tok-stored",
            "tskey-auth-stored",
        )
        .unwrap()
    }

    #[test]
    fn save_load_clear_round_trips() {
        let mut store = InMemoryConfigStore::default();
        assert_eq!(store.load().unwrap(), None);

        let config = stored_config();
        store.save(&config).unwrap();
        assert_eq!(store.load().unwrap(), Some(config.clone()));

        store.clear().unwrap();
        assert_eq!(store.load().unwrap(), None);
    }

    #[test]
    fn an_empty_store_falls_back_to_compile_time_values() {
        let store = InMemoryConfigStore::default();
        assert_eq!(load_or_fallback(&store, &fallback()).unwrap(), fallback());
        assert_eq!(
            load_with_source(&store, &fallback()).unwrap(),
            (fallback(), ConfigSource::Fallback)
        );
    }

    #[test]
    fn a_stored_config_beats_the_fallback() {
        let mut store = InMemoryConfigStore::default();
        store.save(&stored_config()).unwrap();
        assert_eq!(
            load_or_fallback(&store, &fallback()).unwrap(),
            stored_config()
        );
        assert_eq!(
            load_with_source(&store, &fallback()).unwrap(),
            (stored_config(), ConfigSource::Stored)
        );
    }

    #[test]
    fn a_cleared_store_falls_back_again() {
        let mut store = InMemoryConfigStore::default();
        store.save(&stored_config()).unwrap();
        store.clear().unwrap();
        assert_eq!(load_or_fallback(&store, &fallback()).unwrap(), fallback());
    }

    #[test]
    fn store_errors_propagate() {
        struct BrokenStore;
        impl DeviceConfigStore for BrokenStore {
            type Error = StoreBroken;

            fn load(&self) -> Result<Option<DeviceConfig>, Self::Error> {
                Err(StoreBroken)
            }

            fn save(&mut self, _: &DeviceConfig) -> Result<(), Self::Error> {
                Err(StoreBroken)
            }

            fn clear(&mut self) -> Result<(), Self::Error> {
                Err(StoreBroken)
            }
        }
        #[derive(Debug)]
        struct StoreBroken;

        let store = BrokenStore;
        assert!(matches!(
            load_or_fallback(&store, &fallback()),
            Err(StoreBroken)
        ));
    }
}
