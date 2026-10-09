# Roadmap

A loose backlog of things that did not make the current round. Nothing here is
promised or scheduled — it's the direction of travel. Ideas and PRs welcome.

## Recently landed

Shipped across recent rounds (visible in the git log and the links from
[README](README.md)):

- **Licensing & hygiene** — MIT `LICENSE` (relicensed from Apache-2.0 in
  v0.1.1) with a third-party `NOTICE` covering the vendored firmware
  components.
- **Python CI** — a workflow running Ruff, the pytest suite (light core) and
  Semgrep on push/PR, with the README badge.
- **Validated configuration** — pydantic-settings with bounds and paired
  validators; `GET/PATCH /api/config`; the `presets/` profiles
  (local-gpu / external-api / cpu-only); secret masking; hot-apply vs
  `restart_required` reporting; `.env` persistence.
- **Modular dashboard** — first-run onboarding wizard with an explicit
  capture-consent gate; a settings page with per-field descriptions,
  connection probes and apply-status chips; a comic archive with story-arc
  timeline; a fullscreen reader.
- **Optional access control** — `WEB_TOKEN` bearer auth for `/api` and the
  event feed, with device-critical paths kept open.
- **Documentation** — [docs/](docs/) (installation, configuration reference,
  self-hosting, troubleshooting) with real UI screenshots and explicit
  executed-vs-inspected verification notes.

## Device & display

- **Multi-device support.** One server, several reTerminals (or other
  displays) in different rooms, each with its own comic feed and audio stream.
- **Alternative display drivers.** The e-paper driver targets the reTerminal
  E1001's 800×480 panel; other SPI e-paper modules (and plain OLED/TFT) would
  widen the hardware zoo it runs on.

## Web UI & experience

- **Scene presets.** The settings page edits per-field values and the
  onboarding preflills the three hardware presets, but one-click *scene*
  presets — "party", "office", "quiet evening" — that set several fields at
  once (cadence, scramble rate, arc bounds) don't exist yet.
- **Empty-vs-set secrets.** Secret-shaped fields render the same masked
  placeholder whether or not they hold a value; a distinct "not set" state
  would make the settings page more legible. (Found during the docs pass;
  reported rather than fixed.)
- **Demo GIFs.** The README now carries real screenshots; short clips of the
  reader and an e-paper refresh cycle are still wanted.

## Security

- **Auth beyond the token.** `WEB_TOKEN` covers `/api` and the event feed.
  Next layers: a reverse-proxy/Tailscale walkthrough for exposure beyond one
  host, rate limiting, and per-device tokens.

## Text & language

- **Non-English scrambler support.** The POS scramble is built on NLTK's
  English Penn-Treebank tagger and Brown corpus pools; other languages need
  language-appropriate taggers and word pools.

## Project plumbing

- **License metadata reconciliation** — resolved: the repository root was
  relicensed to MIT in v0.1.1, now matching the firmware crates'
  `license = "MIT"` declaration.
- **Compose settings ergonomics.** `docker-compose.yml` hardcodes its
  `environment:` block with no `${VAR}` interpolation, so a repo `.env` cannot
  drive container settings without editing the compose file (documented in
  [docs/self-hosting.md](docs/self-hosting.md)); interpolation would close
  that gap. (Found during the docs pass.)
- **Simulator window polish.** The simulator's fixed 840×540 geometry clips
  the status line under the device bezel (visible in
  [docs/images/simulator.png](docs/images/simulator.png)). Cosmetic; found
  during the docs pass.
