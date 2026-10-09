"""Settings validation and env-drift warnings (see server/config.py)."""
import logging

import pytest
from pydantic import ValidationError

from server.config import (
    Settings,
    find_unknown_env_vars,
    format_validation_error,
    settings,
    suggest_alternative,
    warn_on_unknown_env,
)

MISSING_ENV_FILE = "definitely-not-here.env"  # isolate: no repo .env is read


def make(**overrides) -> Settings:
    """Build Settings from explicit values only (no repo .env)."""
    return Settings(_env_file=MISSING_ENV_FILE, **overrides)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Field-name env vars are the deployment interface; clear them so tests
    see defaults even on a machine exporting real ones."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


class TestValidation:
    def test_defaults_load(self):
        s = make()
        assert (s.PORT, s.COMIC_MIN_MINUTES, s.COMIC_MAX_MINUTES) == (8000, 5.0, 1440.0)
        assert s.SECRET_PHRASES == ["alpha protocol"]
        assert (s.DISPLAY_WIDTH, s.DISPLAY_HEIGHT) == (800, 480)  # fixed hardware

    def test_env_var_names_unchanged(self, monkeypatch):
        """The deployment-critical contract: field names ARE the env var names
        (comack.service, docker-compose.yml, .env.example all rely on them)."""
        monkeypatch.setenv("LLM_URL", "http://llm:8080/")
        monkeypatch.setenv("WHISPER_MODEL_SIZE", "small.en")
        monkeypatch.setenv("COMIC_MAX_MINUTES", "720")
        monkeypatch.setenv("POS_SCRAMBLE_RATE", "0.5")
        monkeypatch.setenv("SECRET_PHRASES", "Alpha Protocol,  Blue Moon")
        s = make()
        assert s.LLM_URL == "http://llm:8080/"
        assert s.WHISPER_MODEL_SIZE == "small.en"
        assert s.COMIC_MAX_MINUTES == 720.0
        assert s.POS_SCRAMBLE_RATE == 0.5
        assert s.SECRET_PHRASES == ["alpha protocol", "blue moon"]

    def test_device_enums_rejected_with_choices(self):
        with pytest.raises(ValidationError) as exc:
            make(WHISPER_DEVICE="loud")
        (line,) = format_validation_error(exc.value)
        assert "WHISPER_DEVICE" in line and "'loud'" in line and "cuda" in line

        with pytest.raises(ValidationError) as exc:
            make(FLUX_DEVICE="gpu")
        assert "FLUX_DEVICE" in format_validation_error(exc.value)[0]

    def test_cadence_bounds_rejected_by_name_and_value(self):
        with pytest.raises(ValidationError) as exc:
            make(COMIC_MIN_MINUTES=-5)
        assert "COMIC_MIN_MINUTES" in format_validation_error(exc.value)[0]
        assert "-5" in format_validation_error(exc.value)[0]

        with pytest.raises(ValidationError):
            make(COMIC_MAX_MINUTES=20000)  # over the one-week cap

    def test_inverted_bounds_name_both_vars(self):
        with pytest.raises(ValidationError) as exc:
            make(COMIC_MIN_MINUTES=100, COMIC_MAX_MINUTES=50)
        (line,) = format_validation_error(exc.value)
        assert "COMIC_MIN_MINUTES" in line and "COMIC_MAX_MINUTES" in line

        with pytest.raises(ValidationError) as exc:
            make(ARC_MIN_PANELS=30, ARC_MAX_PANELS=10)
        (line,) = format_validation_error(exc.value)
        assert "ARC_MIN_PANELS" in line and "ARC_MAX_PANELS" in line

    def test_scramble_rate_is_a_fraction(self):
        for bad in (1.5, -0.1):
            with pytest.raises(ValidationError):
                make(POS_SCRAMBLE_RATE=bad)

    def test_no_speech_probability_is_a_fraction(self):
        with pytest.raises(ValidationError):
            make(WHISPER_MAX_NO_SPEECH=2.0)

    def test_port_bounds(self):
        with pytest.raises(ValidationError):
            make(PORT=99999)

    def test_startup_failure_is_readable(self, monkeypatch):
        """An invalid deployment env fails startup naming the var and value."""
        monkeypatch.setenv("WHISPER_DEVICE", "loud")
        with pytest.raises(SystemExit) as exc:
            from server.config import _load_settings
            _load_settings()
        assert "WHISPER_DEVICE" in str(exc.value) and "'loud'" in str(exc.value)


class TestUnknownEnvVars:
    def test_comack_prefixed_unknowns_are_flagged(self):
        env = {"COMIC_CYCLE_MINUTES": "30", "FLUX_TYPO_X": "1", "LLM_URL": "http://x/"}
        assert find_unknown_env_vars(env) == ["COMIC_CYCLE_MINUTES", "FLUX_TYPO_X"]

    def test_generic_and_external_vars_are_not_flagged(self):
        env = {"HOSTNAME": "container", "PYTHONUNBUFFERED": "1", "PATH": "/bin",
               "HF_HUB_OFFLINE": "1", "HOST": "0.0.0.0", "PORT": "8000",
               "DISPLAY_WIDTH": "800", "LLM_URL": "http://x/"}
        assert find_unknown_env_vars(env) == []

    def test_warn_logs_unknown_with_suggestion_and_returns(self, caplog):
        with caplog.at_level(logging.INFO, logger="comack.config"):
            unknown = warn_on_unknown_env({
                "COMIC_CYCLE_MINUTES": "30", "DISPLAY_WIDTH": "999", "LLM_URL": "http://x/"})
        assert unknown == ["COMIC_CYCLE_MINUTES"]
        warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
        assert "COMIC_CYCLE_MINUTES" in warning.getMessage()
        assert "COMIC_MIN_MINUTES" in warning.getMessage()  # drift suggestion
        info = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("DISPLAY_WIDTH" in r.getMessage() and "fixed in code" in r.getMessage() for r in info)

    def test_suggest_alternative(self):
        assert suggest_alternative("COMIC_CYCLE_MINUTES") in ("COMIC_MIN_MINUTES", "COMIC_MAX_MINUTES")
        assert suggest_alternative("TOTALLY_UNRELATED_VAR_XYZ") is None

    def test_default_scan_reads_process_env_and_repo_env_file(self, monkeypatch, caplog):
        """The no-argument call (the lifespan path) merges os.environ with the
        repo .env keys — and must not crash when either is present."""
        monkeypatch.setenv("COMIC_CYCLE_MINUTES", "30")
        unknown = warn_on_unknown_env()
        assert "COMIC_CYCLE_MINUTES" in unknown


def test_singleton_is_a_settings_instance():
    assert isinstance(settings, Settings)
