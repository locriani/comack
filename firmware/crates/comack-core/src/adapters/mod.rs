//! Interface adapters: implement the application ports on top of narrower,
//! framework-facing boundaries (a TCP stream, `embedded-hal` pins and SPI).

pub mod buf;
pub mod buttons;
pub mod comic_service;
pub mod epd;
pub mod http;
pub mod json;
pub mod net;
pub mod portal;
pub mod websocket;

#[cfg(test)]
pub(crate) mod testing;

pub use buttons::{ActiveLowButtons, PressLatch};
pub use comic_service::{ComicApiError, HttpComicService};
pub use epd::{Epd7in5, EpdError};
pub use http::{HttpClient, HttpConfig, HttpError, HttpResponse, Method};
pub use net::{HostResolver, NetError, ServerLocator, TcpConnector, TcpStream};
pub use portal::{decode_form, route, FormDecodeError, FormFields};
pub use websocket::{WebSocket, WebSocketUplink, WsConfig, WsError};
