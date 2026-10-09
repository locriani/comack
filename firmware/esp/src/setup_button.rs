//! The setup button: the front panel's left/back button (GPIO 5) doubles as
//! the way back into the provisioning portal. Held for [`HOLD`] it
//! re-enters setup from any state; the instructions and retry screens name
//! this so the behaviour is discoverable on the device itself. Of the three
//! front buttons (GPIO 4/5/3), back is the least destructive to hold — a
//! long hold is never an accidental navigation.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use esp_idf_svc::hal::gpio::{Input as EspInputMode, PinDriver};
use log::info;

/// How long the button must be held.
pub const HOLD: Duration = Duration::from_secs(3);

/// Sentinel for "not currently held" in `low_since_us`.
///
/// Microseconds are kept in a `u32`: the Xtensa ESP32-S3 target has no
/// 64-bit atomics. The counter wraps every ~71.6 minutes; `wrapping_sub`
/// in `sample` keeps elapsed times correct across a wrap for anything
/// well under that — a 3-second hold qualifies.
const NOT_LOW: u32 = u32::MAX;

/// Shared "held long enough" flag. Whoever owns the pin samples it —
/// the boot check here, the viewer's button tick, the server-retry loop —
/// and a hold spanning [`HOLD`] of wall time trips it once. `main` checks
/// [`Self::tripped`] wherever it can act on it (restart into setup).
#[derive(Debug, Default)]
pub struct SetupHold {
    low_since_us: AtomicU32,
    tripped: AtomicBool,
}

impl SetupHold {
    pub const fn new() -> Self {
        Self {
            low_since_us: AtomicU32::new(NOT_LOW),
            tripped: AtomicBool::new(false),
        }
    }

    /// Feed one level sample, at whatever cadence the owner ticks. Timing
    /// is wall-clock, so sparse sampling (while the viewer blocks in an
    /// e-paper refresh) still times a hold correctly.
    pub fn sample(&self, pressed: bool) {
        if !pressed {
            self.low_since_us.store(NOT_LOW, Ordering::Relaxed);
            return;
        }
        let now = esp_timer_us();
        let since = self.low_since_us.load(Ordering::Relaxed);
        if since == NOT_LOW {
            self.low_since_us.store(now, Ordering::Relaxed);
        } else if now.wrapping_sub(since) >= HOLD.as_micros() as u32 {
            self.tripped.store(true, Ordering::Relaxed);
        }
    }

    /// Whether a qualifying hold has been observed since the last boot.
    pub fn tripped(&self) -> bool {
        self.tripped.load(Ordering::Relaxed)
    }
}

/// True when the button is being held for [`HOLD`] right now.
///
/// Runs before the pin is handed to the viewer's button stack, so the pin
/// must be sampled directly. A press released early does not count; the
/// device boots normally in that case.

pub fn held_now(pin: &mut PinDriver<'_, EspInputMode>) -> bool {
    if !pin.is_low() {
        return false;
    }
    let start = Instant::now();
    loop {
        if start.elapsed() >= HOLD {
            info!("Setup button held at boot; entering provisioning.");
            return true;
        }
        thread::sleep(Duration::from_millis(20));
        if !pin.is_low() {
            return false;
        }
    }
}

/// Microseconds from `esp_timer`, monotonic since boot, truncated to the
/// low 32 bits (see `NOT_LOW` for why that is safe here).
fn esp_timer_us() -> u32 {
    // SAFETY: `esp_timer_get_time` is always safe to call.
    unsafe { esp_idf_svc::sys::esp_timer_get_time() as u32 }
}
