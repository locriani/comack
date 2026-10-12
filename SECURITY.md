# Security Policy

## Supported versions

Security fixes land on `main`. There are no long-lived release branches yet.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

Prefer a [private GitHub security advisory](https://github.com/locriani/comack/security/advisories/new) against this repository. Include:

- a description of the issue and its impact
- steps to reproduce (or a proof of concept)
- any suggested fix, if you have one

You should receive an acknowledgement within a few days. Please give a reasonable window to patch before any public disclosure.

## Deployment notes

Comack is designed for a **private network** (typically a Tailscale tailnet):

- Defaults bind `0.0.0.0` with `WEB_TOKEN` empty: browser and API surfaces are open to anyone who can reach the host. Set `WEB_TOKEN` (and prefer a tailnet or firewall) before exposing the machine beyond a trusted LAN.
- Device-critical paths (`/ws/audio`, comic image/bin fetches, device button presses) stay tokenless so the e-paper appliance can function — treat network reachability as the trust boundary.
- Never commit `.env`, `firmware/esp/src/secrets.rs`, or `firmware/include/secrets.h`. Copy the `*.example` files instead.
- Rotate any Tailscale auth key, Wi‑Fi password, or API token that ever appeared in a commit, paste, or screenshot.
