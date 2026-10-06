//! Desktop implementations of the firmware's ports, so the real use cases
//! can run against a Comack server without the device: a development tool
//! (`cargo run -p comack-sim`) and the basis of end-to-end tests.

pub mod buttons;
pub mod clock;
pub mod display;
pub mod microphone;
pub mod net;

pub use buttons::{ScriptedButtons, StdinButtons};
pub use clock::StdClock;
pub use display::PbmDisplay;
pub use microphone::SyntheticMicrophone;
pub use net::{StdConnector, StdStream, SystemResolver};
