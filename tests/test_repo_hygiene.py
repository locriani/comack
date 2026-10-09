"""Repo-hygiene guard: the owner's server hostname stays out of the repo.

The original deployment ran on a machine whose hostname became baked into
packaging, defaults, scripts, and docs — owner infrastructure, not a product
concept. This scan keeps that name from returning: any tracked file whose
content matches the pattern fails CI with the offending paths. The pattern
cannot match the word "desktop", so no exclusion list is needed.
"""

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"desk[-_ ]?server", re.IGNORECASE)


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def test_owner_hostname_is_gone():
    offenders = [
        path
        for path in tracked_files()
        if PATTERN.search((ROOT / path).read_text(errors="ignore"))
    ]
    assert not offenders, f"owner hostname leaked into: {offenders}"


def test_local_inference_extra_exists():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    assert "local-inference" in extras
    # Assert on the pattern rather than the old extra name so this file
    # never trips the scan above.
    assert not [name for name in extras if PATTERN.search(name)]
