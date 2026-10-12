//! The setup gesture: all three front buttons (GPIO 4/5/3) held together for
//! [`HOLD`] re-enters the provisioning portal from any state. Nobody does that
//! by accident, so the Wi-Fi settings cannot be wiped by a stray press; the
//! instructions and retry screens name the gesture so it is discoverable on
//! the device itself.
//!
//! A dedicated poller times the hold (so a Spectra 6 refresh cannot starve
//! it), and on trip leaves an NVS flag then restarts — the next boot opens
//! the portal without holding through the reboot.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use esp_idf_svc::hal::gpio::{Input as EspInputMode, PinDriver};
use esp_idf_svc::nvs::{EspDefaultNvsPartition, EspNvs};
use log::{info, warn};

/// How long all three buttons must be held.
pub const HOLD: Duration = Duration::from_secs(10);

/// Ignore brief releases so contact bounce cannot reset a nearly-done hold.
const RELEASE_GRACE: Duration = Duration::from_millis(200);

/// Front-panel GPIOs (active low), matching `main` / the board wiki.
const GPIO_FORWARD: i32 = 4;
const GPIO_BACK: i32 = 5;
const GPIO_TRIGGER: i32 = 3;

/// Sentinel for "not currently held" in `low_since_us`.
///
/// Microseconds are kept in a `u32`: the Xtensa ESP32-S3 target has no
/// 64-bit atomics. The counter wraps every ~71.6 minutes; `wrapping_sub`
/// in `sample` keeps elapsed times correct across a wrap for anything
/// well under that — a 10-second hold qualifies.
const NOT_LOW: u32 = u32::MAX;

/// NVS namespace/key for "open the portal on the next boot".
const REQUEST_NAMESPACE: &str = "comack_boot";
const REQUEST_KEY: &str = "setup";

static NVS: OnceLock<EspDefaultNvsPartition> = OnceLock::new();

/// Remembers the partition so the poller can leave a setup request without
/// borrowing the config store.
pub fn init(nvs: &EspDefaultNvsPartition) {
    let _ = NVS.set(nvs.clone());
}

/// Asks the next boot to open the portal even if the buttons are released.
pub fn request_on_next_boot() {
    let Some(nvs) = NVS.get() else {
        warn!("Setup request skipped: NVS partition not initialised");
        return;
    };
    match EspNvs::new(nvs.clone(), REQUEST_NAMESPACE, true) {
        Ok(ns) => {
            if let Err(e) = ns.set_u8(REQUEST_KEY, 1) {
                warn!("Could not leave the setup request: {e}");
            }
        }
        Err(e) => warn!("Could not open the setup request namespace: {e}"),
    }
}

/// Whether a previous run asked for the portal; clears the request.
pub fn take_request(nvs: &EspDefaultNvsPartition) -> bool {
    let Ok(ns) = EspNvs::new(nvs.clone(), REQUEST_NAMESPACE, true) else {
        return false;
    };
    let requested = matches!(ns.get_u8(REQUEST_KEY), Ok(Some(1)));
    if requested {
        let _ = ns.remove(REQUEST_KEY);
        info!("Setup requested by the previous run; entering provisioning.");
    }
    requested
}

/// Shared "held long enough" flag. The GPIO poller is the sole sampler so
/// competing reads cannot reset the timer. `main` may also check
/// [`Self::tripped`]; the poller restarts as soon as the hold qualifies.
#[derive(Debug, Default)]
pub struct SetupHold {
    low_since_us: AtomicU32,
    /// First sample that saw a release while a hold was in progress; `NOT_LOW`
    /// means "still held or idle".
    release_since_us: AtomicU32,
    tripped: AtomicBool,
}

impl SetupHold {
    pub const fn new() -> Self {
        Self {
            low_since_us: AtomicU32::new(NOT_LOW),
            release_since_us: AtomicU32::new(NOT_LOW),
            tripped: AtomicBool::new(false),
        }
    }

    /// Feed one level sample. Timing is wall-clock.
    pub fn sample(&self, pressed: bool) {
        let now = esp_timer_us();
        if !pressed {
            let since = self.low_since_us.load(Ordering::Relaxed);
            if since == NOT_LOW {
                return;
            }
            let release_since = self.release_since_us.load(Ordering::Relaxed);
            if release_since == NOT_LOW {
                self.release_since_us.store(now, Ordering::Relaxed);
                return;
            }
            if now.wrapping_sub(release_since) < RELEASE_GRACE.as_micros() as u32 {
                return;
            }
            self.low_since_us.store(NOT_LOW, Ordering::Relaxed);
            self.release_since_us.store(NOT_LOW, Ordering::Relaxed);
            return;
        }
        self.release_since_us.store(NOT_LOW, Ordering::Relaxed);
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

    /// Poll the three setup GPIOs every 50 ms and restart into setup as soon
    /// as the hold qualifies — even while the main task is blocked in an
    /// e-paper refresh.
    pub fn spawn_poller(self: &Arc<Self>) {
        let hold = Arc::clone(self);
        if let Err(e) = thread::Builder::new()
            .name("setup-hold".into())
            .spawn(move || loop {
                hold.sample(raw_all_pressed());
                if hold.tripped() {
                    info!("Setup gesture held; restarting into provisioning.");
                    request_on_next_boot();
                    // SAFETY: clean restart into provisioning.
                    unsafe { esp_idf_svc::sys::esp_restart() }
                }
                thread::sleep(Duration::from_millis(50));
            })
        {
            warn!("Setup-hold poller not started: {e}");
        }
    }
}

/// Active-low read of the three front buttons via the raw GPIO matrix —
/// safe alongside the PinDrivers that own the same pins for the viewer.
fn raw_all_pressed() -> bool {
    // SAFETY: gpio_get_level only reads the pin; the pins are already
    // configured as inputs with pull-ups by main.
    unsafe {
        esp_idf_svc::sys::gpio_get_level(GPIO_FORWARD) == 0
            && esp_idf_svc::sys::gpio_get_level(GPIO_BACK) == 0
            && esp_idf_svc::sys::gpio_get_level(GPIO_TRIGGER) == 0
    }
}

/// True when all three buttons are being held for [`HOLD`] right now.
///
/// Runs before the pins are handed to the viewer's button stack, so they
/// must be sampled directly. A button released early does not count; the
/// device boots normally in that case.
pub fn held_now(pins: &mut [PinDriver<'_, EspInputMode>; 3]) -> bool {
    let all_down =
        |pins: &mut [PinDriver<'_, EspInputMode>; 3]| pins.iter_mut().all(|pin| pin.is_low());
    if !all_down(pins) {
        return false;
    }
    let start = Instant::now();
    loop {
        if start.elapsed() >= HOLD {
            info!("All buttons held at boot; entering provisioning.");
            return true;
        }
        thread::sleep(Duration::from_millis(20));
        if !all_down(pins) {
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
