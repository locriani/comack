from unittest.mock import patch
import pytest
from server.render import flux_generator as fg


def run(outcomes):
    """generate_panel_art where each local attempt raises/returns the next outcome; returns (result, sleeps)."""
    it, sleeps = iter(outcomes), []

    def attempt(*_):
        o = next(it)
        if isinstance(o, Exception):
            raise o
        return o

    with patch.object(fg.FluxGenerator, "_generate_local", side_effect=attempt), \
         patch.object(fg, "release_pipeline"), patch.object(fg.time, "sleep", sleeps.append):
        return fg.flux_generator.generate_panel_art("x", (8, 8)), sleeps


def test_retries_with_backoff_then_succeeds():
    result, sleeps = run([RuntimeError("CUDA out of memory"), RuntimeError("wrong device"), "art"])
    assert result == "art" and sleeps == list(fg.RETRY_DELAYS_S[:2])


def test_gives_up_after_every_retry():
    with pytest.raises(fg.PanelArtFailed):
        run([RuntimeError("oom")] * (len(fg.RETRY_DELAYS_S) + 1))


def test_no_pipeline_at_all_falls_back_without_retrying():
    assert run([fg.FluxUnavailable("no torch")]) == (None, [])
