//! Entities and pure business rules. Nothing here performs I/O.

pub mod audio;
pub mod buttons;
pub mod device_config;
pub mod frame;
pub mod issue;
pub mod led;
pub mod navigator;
pub mod text;
pub mod time;
pub mod wifi;

pub use audio::{PeakMeter, StreamReport, StreamStats};
pub use buttons::{Button, ButtonLevels, ButtonPanel, EdgeDetector, Presses};
pub use device_config::{
    ConfigError, DeviceConfig, AUTH_KEY_MAX_LEN, DEVICE_NAME_MAX_LEN, ENCODED_MAX,
    SERVER_HOST_MAX_LEN,
};
pub use frame::{black_pixel_count, Frame, FRAME_BYTES, FRAME_HEIGHT, FRAME_PIXELS, FRAME_WIDTH};
pub use issue::{Direction, Issue};
pub use led::LedDwell;
pub use navigator::{BrowseTargets, Navigator};
pub use text::{draw_text, draw_text_scaled, CHAR_ADVANCE, GLYPH_HEIGHT, GLYPH_WIDTH};
pub use time::Instant;
pub use wifi::{NetworkRotation, WifiCredentials, MAX_NETWORKS, PASSWORD_MAX_LEN, SSID_MAX_LEN};
