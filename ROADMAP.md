# Roadmap

A loose backlog of things that did not make the current round. Nothing here is
promised or scheduled — it's the direction of travel. Ideas and PRs welcome.

## Device & display

- **Multi-device support.** One server, several reTerminals (or other
  displays) in different rooms, each with its own comic feed and audio stream.
- **Alternative display drivers.** The e-paper driver targets the reTerminal
  E1001's 800×480 panel; other SPI e-paper modules (and plain OLED/TFT) would
  widen the hardware zoo it runs on.

## Web UI & experience

- **Config preset picker in the dashboard.** Edit cadence, scramble rate and
  arc bounds from the web UI with sensible presets ("party", "office",
  "quiet evening") instead of hand-editing `.env`.
- **Demo GIFs.** Short clips of the dashboard, the reader, and an e-paper
  refresh, for the README.

## Security

- **Auth hardening.** The server currently assumes a trusted home network:
  `/api/trigger`, the reader and the WebSocket endpoints are unauthenticated.
  Add at least a shared-token mode for deployments that reach beyond one LAN.

## Text & language

- **Non-English scrambler support.** The POS scramble is built on NLTK's
  English Penn-Treebank tagger and Brown corpus pools; other languages need
  language-appropriate taggers and word pools.

## Project plumbing

- **Python CI.** A workflow running the pytest suite (light core) on push,
  mirroring the firmware workflow — plus the resulting README badge.
- **License metadata reconciliation.** The firmware crates declare
  `license = "MIT"` while the repository root is Apache-2.0 (both permissive;
  see [NOTICE](NOTICE)) — settle on a single declaration.
