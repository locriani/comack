//! Just enough JSON for `/api/comics/current`.

/// The value of the first `"issue_number"` key in a JSON body, if it is a
/// non-negative integer that fits in a `u32`.
pub fn parse_issue_number(body: &[u8]) -> Option<u32> {
    const KEY: &[u8] = b"\"issue_number\"";
    let start = body.windows(KEY.len()).position(|w| w == KEY)? + KEY.len();
    let rest = skip_whitespace(&body[start..]);
    let rest = skip_whitespace(rest.strip_prefix(b":")?);
    let digits = rest.iter().take_while(|b| b.is_ascii_digit()).count();
    if digits == 0 {
        return None;
    }
    rest[..digits].iter().try_fold(0u32, |n, d| {
        n.checked_mul(10)?.checked_add(u32::from(d - b'0'))
    })
}

fn skip_whitespace(bytes: &[u8]) -> &[u8] {
    let n = bytes.iter().take_while(|b| b.is_ascii_whitespace()).count();
    &bytes[n..]
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn reads_compact_server_output() {
        let body = br#"{"id":3,"issue_number":42,"series_issue":2,"title":"x"}"#;
        assert_eq!(parse_issue_number(body), Some(42));
    }

    #[test]
    fn tolerates_whitespace() {
        assert_eq!(parse_issue_number(b"{ \"issue_number\" :\n 7 }"), Some(7));
    }

    #[test]
    fn rejects_missing_or_non_numeric_values() {
        assert_eq!(
            parse_issue_number(br#"{"detail":"No displayed comic yet"}"#),
            None
        );
        assert_eq!(parse_issue_number(br#"{"issue_number":null}"#), None);
        assert_eq!(parse_issue_number(br#"{"issue_number":-3}"#), None);
        assert_eq!(parse_issue_number(br#"{"issue_number" 3}"#), None);
        assert_eq!(parse_issue_number(br#"{"issue_number":99999999999}"#), None);
        assert_eq!(parse_issue_number(b""), None);
    }

    #[test]
    fn similar_keys_do_not_match() {
        assert_eq!(parse_issue_number(br#"{"series_issue":5}"#), None);
        assert_eq!(
            parse_issue_number(br#"{"series_issue":5,"issue_number":6}"#),
            Some(6)
        );
    }

    proptest! {
        #[test]
        fn round_trips_any_u32(n in any::<u32>()) {
            let body = std::format!(r#"{{"title":"t","issue_number":{n},"x":1}}"#);
            prop_assert_eq!(parse_issue_number(body.as_bytes()), Some(n));
        }

        #[test]
        fn never_panics(body in proptest::collection::vec(any::<u8>(), 0..256)) {
            let _ = parse_issue_number(&body);
        }
    }
}
