//! Wi-Fi network rotation: try the configured networks in turn.

/// One network the device may join.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WifiCredentials<'a> {
    /// Network name.
    pub ssid: &'a str,
    /// Pre-shared key; empty for an open network.
    pub password: &'a str,
}

/// Cycles through a primary network and its fallbacks, moving on every time
/// the current one disconnects or fails to connect.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NetworkRotation<'a> {
    networks: &'a [WifiCredentials<'a>],
    index: usize,
}

impl<'a> NetworkRotation<'a> {
    /// Starts at the first (primary) network; `None` if there are none.
    pub const fn new(networks: &'a [WifiCredentials<'a>]) -> Option<Self> {
        if networks.is_empty() {
            None
        } else {
            Some(Self { networks, index: 0 })
        }
    }

    /// The network to use now.
    pub fn current(&self) -> &'a WifiCredentials<'a> {
        &self.networks[self.index]
    }

    /// Position of the current network in the list.
    pub const fn index(&self) -> usize {
        self.index
    }

    /// Move to the next network, wrapping around, and return it.
    pub fn advance(&mut self) -> &'a WifiCredentials<'a> {
        self.index = (self.index + 1) % self.networks.len();
        self.current()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const HOME: WifiCredentials<'static> = WifiCredentials {
        ssid: "home",
        password: "pw",
    };
    const PHONE: WifiCredentials<'static> = WifiCredentials {
        ssid: "phone",
        password: "",
    };

    #[test]
    fn requires_at_least_one_network() {
        assert!(NetworkRotation::new(&[]).is_none());
    }

    #[test]
    fn rotates_and_wraps() {
        let networks = [HOME, PHONE];
        let mut rotation = NetworkRotation::new(&networks).unwrap();
        assert_eq!(rotation.current(), &HOME);
        assert_eq!(rotation.advance(), &PHONE);
        assert_eq!(rotation.index(), 1);
        assert_eq!(rotation.advance(), &HOME);
        assert_eq!(rotation.index(), 0);
    }

    #[test]
    fn single_network_retries_itself() {
        let networks = [HOME];
        let mut rotation = NetworkRotation::new(&networks).unwrap();
        assert_eq!(rotation.advance(), &HOME);
    }
}
