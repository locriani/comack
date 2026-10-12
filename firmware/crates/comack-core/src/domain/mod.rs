//! Entities and pure business rules. Nothing here performs I/O.

pub mod audio;
pub mod buttons;
pub mod chord_guard;
pub mod device_config;
pub mod frame;
pub mod issue;
pub mod led;
pub mod navigator;
pub mod qr;
pub mod setup_password;
pub mod text;
pub mod time;
pub mod wifi;

pub use audio::{PeakMeter, StreamReport, StreamStats};
pub use buttons::{Button, ButtonLevels, ButtonPanel, EdgeDetector, Presses};
pub use chord_guard::{ChordGuard, CHORD_GRACE};
pub use device_config::{
    ConfigError, DeviceConfig, AUTH_KEY_MAX_LEN, DEVICE_NAME_MAX_LEN, ENCODED_MAX,
    SERVER_HOST_MAX_LEN,
};
pub use frame::{
    black_pixel_count, ColorFrame, Frame, COLOR_FRAME_BYTES, FRAME_BYTES, FRAME_HEIGHT,
    FRAME_PIXELS, FRAME_WIDTH,
};
pub use issue::{Direction, Issue};
pub use led::LedDwell;
pub use navigator::{BrowseTargets, Navigator};
pub use qr::{draw_qr, wifi_join_payload};
pub use setup_password::{generate_setup_password, SetupPassword, SETUP_PASSWORD_LEN};
pub use text::{draw_text, draw_text_scaled, CHAR_ADVANCE, GLYPH_HEIGHT, GLYPH_WIDTH};
pub use time::Instant;
pub use wifi::{NetworkRotation, WifiCredentials, MAX_NETWORKS, PASSWORD_MAX_LEN, SSID_MAX_LEN};
