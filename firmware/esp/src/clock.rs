//! [`Clock`] from the ESP high-resolution timer (microseconds since boot).

use comack_core::application::ports::Clock;
use comack_core::domain::Instant;
use esp_idf_svc::systime::EspSystemTime;

#[derive(Clone, Copy, Debug, Default)]
pub struct EspClock;

impl Clock for EspClock {
    fn now(&self) -> Instant {
        Instant::from_elapsed(EspSystemTime.now())
    }
}
