"""Repo-hygiene guard: owner infrastructure and live secrets stay out of the tree.

Scans every tracked file for:
- a known owner hostname pattern used in early packaging/docs
- Tailscale auth keys that look real (long tskey-auth-… tokens), not placeholders
"""

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOSTNAME = re.compile(r"desk[-_ ]?server", re.IGNORECASE)
# Real keys are long; placeholders use XXXX / REPLACE_ME / sample / …
REAL_TSKEY = re.compile(r"tskey-auth-[A-Za-z0-9_-]{20,}")
TSKEY_PLACEHOLDER = re.compile(
    r"tskey-auth-(?:XXXX|REPLACE_ME|sample|fallback|stored|x|REDACTED)\b",
    re.IGNORECASE,
)
# This file documents the hostname pattern in a regex — skip self-matches.
SKIP_PATHS = {"tests/test_repo_hygiene.py"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line and line not in SKIP_PATHS]


def _text(path: str) -> str:
    return (ROOT / path).read_text(errors="ignore")


def test_owner_hostname_is_gone():
    offenders = [path for path in tracked_files() if HOSTNAME.search(_text(path))]
    assert not offenders, f"owner hostname leaked into: {offenders}"


def test_no_live_tailscale_auth_keys():
    offenders = []
    for path in tracked_files():
        text = _text(path)
        for match in REAL_TSKEY.finditer(text):
            token = match.group(0)
            if TSKEY_PLACEHOLDER.fullmatch(token):
                continue
            offenders.append(f"{path}: {token[:20]}…")
    assert not offenders, f"live Tailscale auth key leaked into: {offenders}"


def test_local_inference_extra_exists():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    assert "local-inference" in extras
    assert not [name for name in extras if HOSTNAME.search(name)]


def test_runtime_comics_not_tracked():
    tracked = [p for p in tracked_files() if p.startswith("data/comics/") and not p.endswith(".gitkeep")]
    assert not tracked, f"runtime comics must stay untracked: {tracked}"
