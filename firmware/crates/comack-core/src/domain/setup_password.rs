//! The WPA2 passphrase of the setup access point: random per portal run, so
//! the network is never open and never shares a fixed secret.

use heapless::String;

/// Characters in a setup password: 10 × 5 bits = 50 bits, enough for a
/// short-lived AP that serves one form.
pub const SETUP_PASSWORD_LEN: usize = 10;

/// 32 symbols, so `% 32` over a uniform `u32` is unbiased. Digits and
/// letters that read alike on a small e-paper (0/O, 1/I/L) are left out.
const ALPHABET: &[u8; 32] = b"23456789ABCDEFGHJKMNPQRSTUVWXYZa";

/// A setup passphrase (WPA2 needs 8–63 ASCII characters).
pub type SetupPassword = String<SETUP_PASSWORD_LEN>;

/// Draws a password from `random`, which must yield uniformly random `u32`s
/// (the ESP's hardware RNG, once the radio is on).
pub fn generate_setup_password(mut random: impl FnMut() -> u32) -> SetupPassword {
    let mut password = SetupPassword::new();
    for _ in 0..SETUP_PASSWORD_LEN {
        let symbol = ALPHABET[(random() % 32) as usize] as char;
        // Cannot fail: exactly SETUP_PASSWORD_LEN pushes into that capacity.
        let _ = password.push(symbol);
    }
    password
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wpa2_length_and_charset() {
        let mut n = 0u32;
        let password = generate_setup_password(|| {
            n = n.wrapping_add(0x9E37_79B9);
            n
        });
        assert_eq!(password.len(), SETUP_PASSWORD_LEN);
        assert!((8..=63).contains(&password.len()));
        assert!(password.bytes().all(|b| ALPHABET.contains(&b)));
    }

    #[test]
    fn every_symbol_is_reachable_and_ambiguous_ones_are_not() {
        for (i, expected) in ALPHABET.iter().enumerate() {
            let password = generate_setup_password(|| i as u32);
            assert!(password.bytes().all(|b| b == *expected));
        }
        for bad in b"0O1IlL" {
            assert!(!ALPHABET.contains(bad));
        }
    }

    #[test]
    fn different_randomness_gives_different_passwords() {
        let mut a = 1u32;
        let mut b = 2u32;
        let first = generate_setup_password(|| {
            a = a.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
            a >> 7
        });
        let second = generate_setup_password(|| {
            b = b.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
            b >> 7
        });
        assert_ne!(first, second);
    }
}
