//! Driver for the reTerminal E1001's 7.5" 800x480 e-paper panel
//! (UC8179-class controller, Waveshare 7.5" V2 compatible), written against
//! `embedded-hal` 1.0 so it is independent of the MCU.
//!
//! The init sequence is the one proven on this board; it deliberately
//! differs from `epd-waveshare`'s `epd7in5_v2` (no booster soft-start or PLL
//! commands, VCOM before TCON, power setting `07 07 3F 3F`).

use embedded_hal::delay::DelayNs;
use embedded_hal::digital::{InputPin, OutputPin};
use embedded_hal::spi::SpiDevice;
use log::{info, warn};

use crate::application::ports::{ColorFrameDisplay, FrameDisplay};
use crate::domain::{ColorFrame, Frame, FRAME_HEIGHT, FRAME_WIDTH};

/// Controller commands used by the refresh cycle.
pub mod cmd {
    /// Panel setting.
    pub const PANEL_SETTING: u8 = 0x00;
    /// Power setting.
    pub const POWER_SETTING: u8 = 0x01;
    /// Power off.
    pub const POWER_OFF: u8 = 0x02;
    /// Power on.
    pub const POWER_ON: u8 = 0x04;
    /// Deep sleep.
    pub const DEEP_SLEEP: u8 = 0x07;
    /// Display refresh.
    pub const DISPLAY_REFRESH: u8 = 0x12;
    /// New-data RAM write.
    pub const DATA_START_2: u8 = 0x13;
    /// Dual SPI mode.
    pub const DUAL_SPI: u8 = 0x15;
    /// VCOM and data interval setting.
    pub const VCOM_DATA_INTERVAL: u8 = 0x50;
    /// TCON setting.
    pub const TCON_SETTING: u8 = 0x60;
    /// Resolution setting.
    pub const RESOLUTION_SETTING: u8 = 0x61;
    /// Deep-sleep check code that must follow [`DEEP_SLEEP`].
    pub const DEEP_SLEEP_CHECK: u8 = 0xA5;
}

/// Pixel data is streamed in chunks of this many bytes (an ESP32-S3 SPI
/// transaction tops out at 32 KiB, and the chunk also bounds the inversion
/// buffer).
pub const CHUNK_BYTES: usize = 4096;

/// Resolution setting payload: width then height, big-endian.
#[allow(clippy::cast_possible_truncation)] // 800 and 480 fit in u16
const RESOLUTION: [u8; 4] = {
    let [w_hi, w_lo] = (FRAME_WIDTH as u16).to_be_bytes();
    let [h_hi, h_lo] = (FRAME_HEIGHT as u16).to_be_bytes();
    [w_hi, w_lo, h_hi, h_lo]
};

/// How often BUSY is polled, and how long before giving up on it.
const BUSY_POLL_MS: u32 = 10;
const BUSY_TIMEOUT_MS: u32 = 20_000;
/// A Spectra 6 colour refresh takes 14-19 s, so it needs a longer leash.
const SPECTRA6_BUSY_TIMEOUT_MS: u32 = 40_000;

/// Spectra 6 (reTerminal E1002) init sequence: command, then its parameters.
/// Register values from ESPHome's `Seeed-reTerminal-E1002` model.
const SPECTRA6_INIT: [(u8, &[u8]); 13] = [
    (0xAA, &[0x49, 0x55, 0x20, 0x08, 0x09, 0x18]),
    (0x01, &[0x3F]),
    (0x00, &[0x5F, 0x69]),
    (0x03, &[0x00, 0x54, 0x00, 0x44]),
    (0x05, &[0x40, 0x1F, 0x1F, 0x2C]),
    (0x06, &[0x6F, 0x1F, 0x17, 0x49]),
    (0x08, &[0x6F, 0x1F, 0x1F, 0x22]),
    (0x30, &[0x03]),
    (0x50, &[0x3F]),
    (0x60, &[0x02, 0x00]),
    (cmd::RESOLUTION_SETTING, &RESOLUTION),
    (0x84, &[0x01]),
    (0xE3, &[0x2F]),
];
/// Spectra 6 pixel data: 4 bits per pixel, even pixel in the high nibble.
const SPECTRA6_DATA: u8 = 0x10;

/// Why a refresh failed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EpdError<SpiE, PinE> {
    /// SPI transfer failed.
    Spi(SpiE),
    /// A control pin could not be driven or read.
    Pin(PinE),
}

/// The panel. `spi` owns chip-select; `busy` is low while the controller
/// is working. Monochrome 7.5" (E1001) by default; [`Self::spectra6`] switches
/// to the 7.3" colour panel (E1002), which has the same wiring.
pub struct Epd7in5<SPI, DC, RST, BUSY, D> {
    spi: SPI,
    dc: DC,
    rst: RST,
    busy: BUSY,
    delay: D,
    chunk: [u8; CHUNK_BYTES],
    spectra6: bool,
}

/// What a Spectra 6 refresh uploads.
#[derive(Clone, Copy)]
enum Spectra6Pixels<'a> {
    /// A 1-bit frame, expanded to black and white on the way.
    Mono(&'a Frame),
    /// A colour image already packed for the panel.
    Colour(&'a ColorFrame),
}

/// Expands 1-bit pixels (1 = white, MSB leftmost) to Spectra 6 nibbles
/// (0x1 white, 0x0 black), two pixels per byte, even pixel high. `out` must
/// hold four bytes per source byte.
fn expand_to_nibbles(src: &[u8], out: &mut [u8]) {
    for (quad, byte) in out.chunks_exact_mut(4).zip(src) {
        for (k, dst) in quad.iter_mut().enumerate() {
            let even = (byte >> (7 - 2 * k)) & 1;
            let odd = (byte >> (6 - 2 * k)) & 1;
            *dst = even << 4 | odd;
        }
    }
}

type Result<T, SPI, PIN> =
    core::result::Result<T, EpdError<<SPI as embedded_hal::spi::ErrorType>::Error, PIN>>;

impl<SPI, DC, RST, BUSY, D, PinE> Epd7in5<SPI, DC, RST, BUSY, D>
where
    SPI: SpiDevice,
    DC: OutputPin<Error = PinE>,
    RST: OutputPin<Error = PinE>,
    BUSY: InputPin<Error = PinE>,
    D: DelayNs,
{
    /// Wrap configured peripherals. Nothing is sent until the first frame.
    pub fn new(spi: SPI, dc: DC, rst: RST, busy: BUSY, delay: D) -> Self {
        Self {
            spi,
            dc,
            rst,
            busy,
            delay,
            chunk: [0; CHUNK_BYTES],
            spectra6: false,
        }
    }

    /// Drive the 7.3" Spectra 6 colour panel (reTerminal E1002) instead of
    /// the monochrome one. 1-bit frames still show as black and white;
    /// colour images go through [`Self::display_color`].
    #[must_use]
    pub fn spectra6(mut self) -> Self {
        self.spectra6 = true;
        self
    }

    /// Full refresh cycle: wake, initialise, upload, refresh, deep sleep.
    ///
    /// # Errors
    /// SPI or pin failures; a BUSY timeout is only logged.
    pub fn display_frame(&mut self, frame: &Frame) -> Result<(), SPI, PinE> {
        info!("Starting e-paper refresh cycle...");
        self.reset()?;
        self.wait_busy()?;
        if self.spectra6 {
            return self.display_spectra6(Spectra6Pixels::Mono(frame));
        }

        // VGH/VGL 20 V, VDH/VDL 15 V.
        self.command(cmd::POWER_SETTING, &[0x07, 0x07, 0x3F, 0x3F])?;
        self.command(cmd::POWER_ON, &[])?;
        self.delay.delay_ms(100);
        self.wait_busy()?;

        // KW mode, LUT from OTP.
        self.command(cmd::PANEL_SETTING, &[0x1F])?;
        self.command(cmd::RESOLUTION_SETTING, &RESOLUTION)?;
        self.command(cmd::DUAL_SPI, &[0x00])?;
        self.command(cmd::VCOM_DATA_INTERVAL, &[0x10, 0x07])?;
        self.command(cmd::TCON_SETTING, &[0x22])?;

        // 1-bit mode uses the new-data register only.
        self.command(cmd::DATA_START_2, &[])?;
        self.pixels(frame)?;

        self.command(cmd::DISPLAY_REFRESH, &[])?;
        self.delay.delay_ms(100);
        self.wait_busy()?;
        info!("Display refreshed successfully.");
        self.sleep()
    }

    /// Power the panel down into deep sleep.
    ///
    /// # Errors
    /// SPI or pin failures.
    pub fn sleep(&mut self) -> Result<(), SPI, PinE> {
        let power_off: &[u8] = if self.spectra6 { &[0x00] } else { &[] };
        self.command(cmd::POWER_OFF, power_off)?;
        self.wait_busy()?;
        self.command(cmd::DEEP_SLEEP, &[cmd::DEEP_SLEEP_CHECK])
    }

    /// Full refresh with a colour image: 4 bits per pixel in the panel's
    /// native packing (see [`ColorFrame`]). Spectra 6 panels only; a
    /// monochrome panel cannot show it, so the call is refused with a warning.
    ///
    /// # Errors
    /// SPI or pin failures; a BUSY timeout is only logged.
    pub fn display_color(&mut self, image: &ColorFrame) -> Result<(), SPI, PinE> {
        if !self.spectra6 {
            warn!("Colour frame ignored: panel is monochrome");
            return Ok(());
        }
        info!("Starting e-paper colour refresh cycle...");
        self.reset()?;
        self.wait_busy()?;
        self.display_spectra6(Spectra6Pixels::Colour(image))
    }

    /// Spectra 6 cycle, after reset: init, upload, power on, refresh. The
    /// caller's `sleep` powers off.
    fn display_spectra6(&mut self, pixels: Spectra6Pixels<'_>) -> Result<(), SPI, PinE> {
        for (command, data) in SPECTRA6_INIT {
            self.command(command, data)?;
        }
        self.wait_busy()?;

        self.command(SPECTRA6_DATA, &[])?;
        self.pixels_spectra6(pixels)?;

        self.command(cmd::POWER_ON, &[])?;
        self.wait_busy()?;
        self.command(cmd::DISPLAY_REFRESH, &[0x00])?;
        self.wait_busy()?;
        info!("Display refreshed successfully.");
        self.sleep()
    }

    /// Release the peripherals.
    pub fn release(self) -> (SPI, DC, RST, BUSY, D) {
        (self.spi, self.dc, self.rst, self.busy, self.delay)
    }

    fn reset(&mut self) -> Result<(), SPI, PinE> {
        self.rst.set_high().map_err(EpdError::Pin)?;
        self.delay.delay_ms(20);
        self.rst.set_low().map_err(EpdError::Pin)?;
        // ESPHome holds the Spectra 6 reset low for 200 ms.
        self.delay.delay_ms(if self.spectra6 { 200 } else { 4 });
        self.rst.set_high().map_err(EpdError::Pin)?;
        self.delay.delay_ms(20);
        Ok(())
    }

    fn wait_busy(&mut self) -> Result<(), SPI, PinE> {
        let timeout = if self.spectra6 {
            SPECTRA6_BUSY_TIMEOUT_MS
        } else {
            BUSY_TIMEOUT_MS
        };
        let mut waited = 0;
        while self.busy.is_low().map_err(EpdError::Pin)? {
            if waited >= timeout {
                warn!("E-paper busy timeout");
                break;
            }
            self.delay.delay_ms(BUSY_POLL_MS);
            waited += BUSY_POLL_MS;
        }
        Ok(())
    }

    fn command(&mut self, command: u8, data: &[u8]) -> Result<(), SPI, PinE> {
        self.dc.set_low().map_err(EpdError::Pin)?;
        self.spi.write(&[command]).map_err(EpdError::Spi)?;
        if !data.is_empty() {
            self.dc.set_high().map_err(EpdError::Pin)?;
            // One transaction per byte, as the controller expects CS to
            // frame each parameter.
            for byte in data {
                self.spi
                    .write(core::slice::from_ref(byte))
                    .map_err(EpdError::Spi)?;
            }
        }
        Ok(())
    }

    /// Frames arrive with 1 = white, but with VCOM/data interval 0x10 the
    /// controller's register 0x13 treats 1 as black, so invert on the way.
    fn pixels(&mut self, frame: &Frame) -> Result<(), SPI, PinE> {
        self.dc.set_high().map_err(EpdError::Pin)?;
        for block in frame.chunks(CHUNK_BYTES) {
            let out = &mut self.chunk[..block.len()];
            for (dst, src) in out.iter_mut().zip(block) {
                *dst = !src;
            }
            self.spi.write(out).map_err(EpdError::Spi)?;
        }
        Ok(())
    }

    /// Streams the pixels to the panel: a 1-bit frame is expanded to 4-bit
    /// pixels a quarter chunk of source bytes at a time (no full-colour
    /// buffer needed); a colour image is already in the panel's packing.
    fn pixels_spectra6(&mut self, pixels: Spectra6Pixels<'_>) -> Result<(), SPI, PinE> {
        self.dc.set_high().map_err(EpdError::Pin)?;
        match pixels {
            Spectra6Pixels::Mono(frame) => {
                for block in frame.chunks(CHUNK_BYTES / 4) {
                    let out = &mut self.chunk[..block.len() * 4];
                    expand_to_nibbles(block, out);
                    self.spi.write(out).map_err(EpdError::Spi)?;
                }
            }
            Spectra6Pixels::Colour(image) => {
                for block in image.chunks(CHUNK_BYTES) {
                    self.spi.write(block).map_err(EpdError::Spi)?;
                }
            }
        }
        Ok(())
    }
}

impl<SPI, DC, RST, BUSY, D, PinE> FrameDisplay for Epd7in5<SPI, DC, RST, BUSY, D>
where
    SPI: SpiDevice,
    DC: OutputPin<Error = PinE>,
    RST: OutputPin<Error = PinE>,
    BUSY: InputPin<Error = PinE>,
    D: DelayNs,
    PinE: core::fmt::Debug,
{
    type Error = EpdError<SPI::Error, PinE>;

    fn show(&mut self, frame: &Frame) -> core::result::Result<(), Self::Error> {
        self.display_frame(frame)
    }
}

impl<SPI, DC, RST, BUSY, D, PinE> ColorFrameDisplay for Epd7in5<SPI, DC, RST, BUSY, D>
where
    SPI: SpiDevice,
    DC: OutputPin<Error = PinE>,
    RST: OutputPin<Error = PinE>,
    BUSY: InputPin<Error = PinE>,
    D: DelayNs,
    PinE: core::fmt::Debug,
{
    fn show_color(&mut self, image: &ColorFrame) -> core::result::Result<(), Self::Error> {
        self.display_color(image)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{COLOR_FRAME_BYTES, FRAME_BYTES};
    use core::convert::Infallible;
    use embedded_hal::digital::ErrorType as PinErrorType;
    use embedded_hal::spi::{ErrorKind, ErrorType as SpiErrorType, Operation};
    use std::cell::{Cell, RefCell};
    use std::rc::Rc;
    use std::vec::Vec;

    /// Everything the panel would observe, in order.
    #[derive(Clone, Debug, PartialEq, Eq)]
    enum Event {
        Command(u8),
        Data(Vec<u8>),
        Reset(bool),
        DelayMs(u32),
    }

    #[derive(Default)]
    struct Bus {
        events: Vec<Event>,
        dc_high: bool,
        /// BUSY reads low (busy) this many more times before going idle.
        busy_reads: u32,
        busy_polls: u32,
        spi_fail_after: Option<usize>,
        spi_writes: usize,
    }

    type Shared = Rc<RefCell<Bus>>;

    struct Spi(Shared);
    struct Dc(Shared);
    struct Rst(Shared);
    struct Busy(Shared);
    struct Delay(Shared);

    impl SpiErrorType for Spi {
        type Error = ErrorKind;
    }

    impl SpiDevice for Spi {
        fn transaction(
            &mut self,
            operations: &mut [Operation<'_, u8>],
        ) -> core::result::Result<(), ErrorKind> {
            let mut bus = self.0.borrow_mut();
            for op in operations {
                let Operation::Write(bytes) = op else {
                    panic!("driver only writes")
                };
                if bus.spi_fail_after == Some(bus.spi_writes) {
                    return Err(ErrorKind::Other);
                }
                bus.spi_writes += 1;
                let event = if bus.dc_high {
                    Event::Data(bytes.to_vec())
                } else {
                    assert_eq!(bytes.len(), 1, "commands are single bytes");
                    Event::Command(bytes[0])
                };
                bus.events.push(event);
            }
            Ok(())
        }
    }

    impl PinErrorType for Dc {
        type Error = Infallible;
    }
    impl OutputPin for Dc {
        fn set_low(&mut self) -> core::result::Result<(), Infallible> {
            self.0.borrow_mut().dc_high = false;
            Ok(())
        }
        fn set_high(&mut self) -> core::result::Result<(), Infallible> {
            self.0.borrow_mut().dc_high = true;
            Ok(())
        }
    }

    impl PinErrorType for Rst {
        type Error = Infallible;
    }
    impl OutputPin for Rst {
        fn set_low(&mut self) -> core::result::Result<(), Infallible> {
            self.0.borrow_mut().events.push(Event::Reset(false));
            Ok(())
        }
        fn set_high(&mut self) -> core::result::Result<(), Infallible> {
            self.0.borrow_mut().events.push(Event::Reset(true));
            Ok(())
        }
    }

    impl PinErrorType for Busy {
        type Error = Infallible;
    }
    impl InputPin for Busy {
        fn is_high(&mut self) -> core::result::Result<bool, Infallible> {
            self.is_low().map(|low| !low)
        }
        fn is_low(&mut self) -> core::result::Result<bool, Infallible> {
            let mut bus = self.0.borrow_mut();
            bus.busy_polls += 1;
            if bus.busy_reads > 0 {
                bus.busy_reads -= 1;
                Ok(true)
            } else {
                Ok(false)
            }
        }
    }

    impl DelayNs for Delay {
        fn delay_ns(&mut self, ns: u32) {
            self.0
                .borrow_mut()
                .events
                .push(Event::DelayMs(ns / 1_000_000));
        }
        fn delay_ms(&mut self, ms: u32) {
            self.0.borrow_mut().events.push(Event::DelayMs(ms));
        }
    }

    fn panel(bus: &Shared) -> Epd7in5<Spi, Dc, Rst, Busy, Delay> {
        Epd7in5::new(
            Spi(bus.clone()),
            Dc(bus.clone()),
            Rst(bus.clone()),
            Busy(bus.clone()),
            Delay(bus.clone()),
        )
    }

    fn checkerboard() -> Frame {
        let mut frame = [0u8; FRAME_BYTES];
        for (i, b) in frame.iter_mut().enumerate() {
            *b = (i * 7 % 256) as u8;
        }
        frame
    }

    /// Commands with their parameter bytes; pixel data and timing stripped.
    fn command_log(events: &[Event]) -> Vec<(u8, Vec<u8>)> {
        let mut log: Vec<(u8, Vec<u8>)> = Vec::new();
        for e in events {
            match e {
                Event::Command(c) => log.push((*c, Vec::new())),
                Event::Data(d) if d.len() == 1 => log.last_mut().unwrap().1.push(d[0]),
                _ => {}
            }
        }
        log
    }

    #[test]
    fn refresh_cycle_sends_the_proven_sequence() {
        let bus = Shared::default();
        panel(&bus).display_frame(&checkerboard()).unwrap();
        let log = command_log(&bus.borrow().events);
        let expected: Vec<(u8, Vec<u8>)> = std::vec![
            (cmd::POWER_SETTING, std::vec![0x07, 0x07, 0x3F, 0x3F]),
            (cmd::POWER_ON, std::vec![]),
            (cmd::PANEL_SETTING, std::vec![0x1F]),
            (cmd::RESOLUTION_SETTING, std::vec![0x03, 0x20, 0x01, 0xE0]),
            (cmd::DUAL_SPI, std::vec![0x00]),
            (cmd::VCOM_DATA_INTERVAL, std::vec![0x10, 0x07]),
            (cmd::TCON_SETTING, std::vec![0x22]),
            (cmd::DATA_START_2, std::vec![]),
            (cmd::DISPLAY_REFRESH, std::vec![]),
            (cmd::POWER_OFF, std::vec![]),
            (cmd::DEEP_SLEEP, std::vec![cmd::DEEP_SLEEP_CHECK]),
        ];
        assert_eq!(log, expected);
    }

    #[test]
    fn starts_with_a_hardware_reset_pulse() {
        let bus = Shared::default();
        panel(&bus).display_frame(&checkerboard()).unwrap();
        let events = &bus.borrow().events;
        assert_eq!(
            events[..6],
            [
                Event::Reset(true),
                Event::DelayMs(20),
                Event::Reset(false),
                Event::DelayMs(4),
                Event::Reset(true),
                Event::DelayMs(20),
            ]
        );
    }

    #[test]
    fn pixels_are_inverted_and_chunked() {
        let bus = Shared::default();
        let frame = checkerboard();
        panel(&bus).display_frame(&frame).unwrap();
        let events = &bus.borrow().events;
        let chunks: Vec<&Vec<u8>> = events
            .iter()
            .filter_map(|e| match e {
                Event::Data(d) if d.len() > 1 => Some(d),
                _ => None,
            })
            .collect();
        assert_eq!(chunks.len(), FRAME_BYTES.div_ceil(CHUNK_BYTES));
        assert!(chunks.iter().all(|c| c.len() <= CHUNK_BYTES));
        let sent: Vec<u8> = chunks.into_iter().flatten().copied().collect();
        let inverted: Vec<u8> = frame.iter().map(|b| !b).collect();
        assert_eq!(sent, inverted);
        // The pixel data sits between DATA_START_2 and DISPLAY_REFRESH.
        let start = events
            .iter()
            .position(|e| *e == Event::Command(cmd::DATA_START_2))
            .unwrap();
        let refresh = events
            .iter()
            .position(|e| *e == Event::Command(cmd::DISPLAY_REFRESH))
            .unwrap();
        assert!(events[start + 1..refresh]
            .iter()
            .all(|e| matches!(e, Event::Data(_))));
    }

    #[test]
    fn expands_one_bit_pixels_to_spectra6_nibbles() {
        let mut out = [0xEE; 8];
        // White, black, white, black, then four black: 0x10 0x10 0x00 0x00.
        expand_to_nibbles(&[0b1010_0000, 0xFF], &mut out);
        assert_eq!(out, [0x10, 0x10, 0x00, 0x00, 0x11, 0x11, 0x11, 0x11]);
    }

    #[test]
    fn spectra6_cycle_sends_init_then_nibbles_then_refresh() {
        let bus = Shared::default();
        let frame = checkerboard();
        let mut epd = Epd7in5::new(
            Spi(bus.clone()),
            Dc(bus.clone()),
            Rst(bus.clone()),
            Busy(bus.clone()),
            Delay(bus.clone()),
        )
        .spectra6();
        epd.display_frame(&frame).unwrap();
        let events = &bus.borrow().events;
        let log = command_log(events);
        let commands: Vec<u8> = log.iter().map(|(c, _)| *c).collect();
        assert_eq!(
            commands,
            [
                0xAA,
                0x01,
                0x00,
                0x03,
                0x05,
                0x06,
                0x08,
                0x30,
                0x50,
                0x60,
                0x61,
                0x84,
                0xE3,
                SPECTRA6_DATA,
                cmd::POWER_ON,
                cmd::DISPLAY_REFRESH,
                cmd::POWER_OFF,
                cmd::DEEP_SLEEP,
            ]
        );
        assert_eq!(log[10].1, [0x03, 0x20, 0x01, 0xE0]); // 800x480
        assert_eq!(log[15].1, [0x00]); // refresh parameter
        assert_eq!(log[16].1, [0x00]); // power-off parameter

        let sent: Vec<u8> = events
            .iter()
            .filter_map(|e| match e {
                Event::Data(d) if d.len() > 1 => Some(d.clone()),
                _ => None,
            })
            .flatten()
            .collect();
        assert_eq!(sent.len(), FRAME_BYTES * 4);
        // First source byte 0 -> eight black pixels.
        assert_eq!(sent[..4], [0, 0, 0, 0]);
    }

    #[test]
    fn color_image_goes_to_the_panel_verbatim() {
        let bus = Shared::default();
        let image: Box<ColorFrame> = (0..COLOR_FRAME_BYTES)
            .map(|i| [0x02, 0x35, 0x61][i % 3])
            .collect::<Vec<u8>>()
            .into_boxed_slice()
            .try_into()
            .unwrap();
        let mut epd = Epd7in5::new(
            Spi(bus.clone()),
            Dc(bus.clone()),
            Rst(bus.clone()),
            Busy(bus.clone()),
            Delay(bus.clone()),
        )
        .spectra6();
        epd.display_color(&image).unwrap();
        let events = &bus.borrow().events;
        let sent: Vec<u8> = events
            .iter()
            .filter_map(|e| match e {
                Event::Data(d) if d.len() > 1 => Some(d.clone()),
                _ => None,
            })
            .flatten()
            .collect();
        assert_eq!(sent.as_slice(), image.as_slice());
        let commands: Vec<u8> = command_log(events).iter().map(|(c, _)| *c).collect();
        assert!(commands.contains(&SPECTRA6_DATA) && commands.contains(&cmd::DISPLAY_REFRESH));
    }

    #[test]
    fn color_image_is_refused_on_the_monochrome_panel() {
        let bus = Shared::default();
        let image: Box<ColorFrame> = std::vec![0u8; COLOR_FRAME_BYTES]
            .into_boxed_slice()
            .try_into()
            .unwrap();
        panel(&bus).display_color(&image).unwrap();
        assert_eq!(bus.borrow().events.len(), 0);
    }

    #[test]
    fn waits_while_busy() {
        let bus = Shared::default();
        bus.borrow_mut().busy_reads = 3;
        panel(&bus).display_frame(&checkerboard()).unwrap();
        let b = bus.borrow();
        let first_cmd = b
            .events
            .iter()
            .position(|e| matches!(e, Event::Command(_)))
            .unwrap();
        let polls_before_first_command = b.events[..first_cmd]
            .iter()
            .filter(|e| **e == Event::DelayMs(BUSY_POLL_MS))
            .count();
        assert_eq!(polls_before_first_command, 3);
    }

    #[test]
    fn gives_up_on_a_stuck_busy_line() {
        let bus = Shared::default();
        bus.borrow_mut().busy_reads = u32::MAX;
        panel(&bus).display_frame(&checkerboard()).unwrap();
        let b = bus.borrow();
        let waits = b
            .events
            .iter()
            .filter(|e| **e == Event::DelayMs(BUSY_POLL_MS))
            .count();
        // Four BUSY waits per cycle, each capped at 20 s of 10 ms polls.
        assert_eq!(waits, 4 * (BUSY_TIMEOUT_MS / BUSY_POLL_MS) as usize);
    }

    #[test]
    fn spi_failure_aborts_the_cycle() {
        let bus = Shared::default();
        bus.borrow_mut().spi_fail_after = Some(20);
        let mut epd = panel(&bus);
        assert_eq!(
            epd.display_frame(&checkerboard()),
            Err(EpdError::Spi(ErrorKind::Other))
        );
        assert!(!bus
            .borrow()
            .events
            .contains(&Event::Command(cmd::DISPLAY_REFRESH)));
    }

    #[test]
    fn implements_the_display_port() {
        let bus = Shared::default();
        let mut display = panel(&bus);
        FrameDisplay::show(&mut display, &checkerboard()).unwrap();
        assert!(bus
            .borrow()
            .events
            .contains(&Event::Command(cmd::DEEP_SLEEP)));
        let polled = Cell::new(bus.borrow().busy_polls);
        assert!(polled.get() >= 4);
        let _ = display.release();
    }
}
