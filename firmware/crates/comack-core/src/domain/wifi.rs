//! Wi-Fi network rotation: try the configured networks in turn.

/// SSID capacity: 32 characters is the 802.11 maximum; the buffer is one
/// slot wider because C Wi-Fi drivers count a terminator.
pub const SSID_MAX_LEN: usize = 33;

/// Password capacity: the WPA2 personal passphrase maximum is 64.
pub const PASSWORD_MAX_LEN: usize = 64;

/// How many networks one configuration may hold, stored or compile-time.
pub const MAX_NETWORKS: usize = 5;

/// One network the device may join. Credentials are owned and bounded so
/// they can come from the runtime config store as well as from compile-time
/// `secrets.rs`; the device never allocates.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WifiCredentials {
    /// Network name.
    pub ssid: heapless::String<SSID_MAX_LEN>,
    /// Pre-shared key; empty for an open network.
    pub password: heapless::String<PASSWORD_MAX_LEN>,
}

impl WifiCredentials {
    /// Builds one network, rejecting values too long for the buffers.
    pub fn new(ssid: &str, password: &str) -> Result<Self, super::device_config::ConfigError> {
        let mut network = Self {
            ssid: heapless::String::new(),
            password: heapless::String::new(),
        };
        network
            .ssid
            .push_str(ssid)
            .map_err(|_| super::device_config::ConfigError::SsidTooLong)?;
        network
            .password
            .push_str(password)
            .map_err(|_| super::device_config::ConfigError::PasswordTooLong)?;
        Ok(network)
    }
}

/// Cycles through a primary network and its fallbacks, moving on every time
/// the current one disconnects or fails to connect.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NetworkRotation {
    networks: heapless::Vec<WifiCredentials, MAX_NETWORKS>,
    index: usize,
}

impl NetworkRotation {
    /// Starts at the first (primary) network; `None` if there are none.
    pub fn new(networks: heapless::Vec<WifiCredentials, MAX_NETWORKS>) -> Option<Self> {
        if networks.is_empty() {
            None
        } else {
            Some(Self { networks, index: 0 })
        }
    }

    /// The network to use now.
    pub fn current(&self) -> &WifiCredentials {
        &self.networks[self.index]
    }

    /// Position of the current network in the list.
    pub const fn index(&self) -> usize {
        self.index
    }

    /// Move to the next network, wrapping around, and return it.
    pub fn advance(&mut self) -> &WifiCredentials {
        self.index = (self.index + 1) % self.networks.len();
        self.current()
    }
}

#[cfg(test)]
mod tests {
    use super::super::device_config::ConfigError;
    use super::*;

    fn home() -> WifiCredentials {
        WifiCredentials::new("home", "pw").unwrap()
    }

    fn phone() -> WifiCredentials {
        WifiCredentials::new("phone", "").unwrap()
    }

    #[test]
    fn rejects_over_long_fields() {
        let long = "x".repeat(SSID_MAX_LEN + 1);
        assert_eq!(
            WifiCredentials::new(&long, ""),
            Err(ConfigError::SsidTooLong)
        );
        assert_eq!(
            WifiCredentials::new("ssid", &"x".repeat(PASSWORD_MAX_LEN + 1)),
            Err(ConfigError::PasswordTooLong)
        );
    }

    #[test]
    fn requires_at_least_one_network() {
        assert!(NetworkRotation::new(heapless::Vec::new()).is_none());
    }

    #[test]
    fn rotates_and_wraps() {
        let mut rotation = NetworkRotation::new(heapless::Vec::from([home(), phone()])).unwrap();
        assert_eq!(rotation.current(), &home());
        assert_eq!(rotation.advance(), &phone());
        assert_eq!(rotation.index(), 1);
        assert_eq!(rotation.advance(), &home());
        assert_eq!(rotation.index(), 0);
    }

    #[test]
    fn single_network_retries_itself() {
        let mut rotation = NetworkRotation::new(heapless::Vec::from([home()])).unwrap();
        assert_eq!(rotation.advance(), &home());
    }
}
