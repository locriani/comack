"""Onboarding wizard: image API step contract + Finish-step merge order.

The image step is OpenAI-compatible (URL / model / API key), same shape as
Story LLM — not gated behind an "External Image API" card. Finish-step merge
must let typed values win over preset sample values (legacy external-api
preset ships ``http://your-llm-host:8080/`` that used to overwrite Story LLM).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS_ROOT = Path(__file__).resolve().parents[1] / "server" / "web" / "static" / "js"
ONBOARDING_JS = JS_ROOT / "views" / "onboarding.js"

# ── wizard contract (static check: the copy names the real endpoint) ─────


def test_image_step_names_the_real_contract():
    src = ONBOARDING_JS.read_text()
    assert "/v1/images/generations" in src   # real endpoint, /v1-aware join
    assert "OpenAI-compatible image" in src
    assert "http://localhost:8000/v1" in src
    assert "POST /generate" not in src       # the invented contract is gone
    assert "8790" not in src
    assert "External Image API" not in src   # no vendor/preset gate for the API fields


def test_image_step_has_key_and_model_fields():
    src = ONBOARDING_JS.read_text()
    assert "boundInput('FLUX_API_MODEL'" in src
    assert "boundInput('FLUX_API_KEY'" in src
    assert "type: 'password'" in src
    # always saved from Finish — not gated on an external-api preset
    finish = src.split("buildFinishUpdates")[1]
    assert "FLUX_API_KEY: state.FLUX_API_KEY.trim()" in finish
    assert "FLUX_API_MODEL: state.FLUX_API_MODEL.trim()" in finish
    assert "api_key: state.FLUX_API_KEY" in src


def test_llm_step_has_api_key_field():
    src = ONBOARDING_JS.read_text()
    assert "OpenAI-compatible" in src
    assert "Use OpenRouter" not in src
    assert "label('API key'" in src
    assert "boundInput('LLM_API_KEY'" in src
    assert "type: 'password'" in src.split("boundInput('LLM_API_KEY'")[1].split(")")[0]
    assert "'LLM_API_KEY'" in src.split("buildFinishUpdates")[1]
    assert "api_key: state.LLM_API_KEY" in src


# ── finish merge (behavioral: execute the real module under Node) ────────

PROBE = """import { buildFinishUpdates, scrubPresetValues } from './views/onboarding.js';
const state = JSON.parse(process.argv[2]);
if (state.__scrub_only) {
  console.log(JSON.stringify(scrubPresetValues(state.presetValues)));
} else {
  console.log(JSON.stringify(buildFinishUpdates(state)));
}
"""


@pytest.fixture()
def wizard_module(tmp_path):
    """The wizard is a browser ES module; copy the tree with a type:module
    manifest so Node can import the real source. No bundler and no DOM — the
    merge helpers are pure, and neither api.js nor auth.js touches browser
    globals at import time."""
    target = tmp_path / "js"
    shutil.copytree(JS_ROOT, target)
    (target / "package.json").write_text('{"type": "module"}')
    probe = target / "finish_probe.mjs"
    probe.write_text(PROBE)
    return probe


def run_finish_merge(wizard_module, **state):
    proc = subprocess.run(
        ["node", str(wizard_module), json.dumps(state)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


EXTERNAL_API_STATE = {
    "preset": "external-api",
    "presetValues": {          # raw preset values, placeholders included
        "LLM_URL": "http://your-llm-host:8080/",
        "FLUX_API_URL": "http://your-image-host:8000",
        "FLUX_DEVICE": "cpu",
        "FLUX_MIN_FREE_VRAM_GB": "0",
    },
    "LLM_URL": "http://typed-llm:1234/v1",
    "LLM_MODEL": "qwen2.5-7b-instruct",
    "LLM_API_KEY": "",
    "WHISPER_MODEL_SIZE": "base.en",
    "FLUX_API_URL": "http://typed-gpu:8000",
    "FLUX_MODEL_ID": "",
    "FLUX_DEVICE": "",
    "FLUX_INFERENCE_STEPS": "",
    "FLUX_MIN_FREE_VRAM_GB": "",
    "FLUX_API_KEY": "",
    "FLUX_API_MODEL": "",
}


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is required to execute the wizard merge logic")
class TestFinishMerge:
    def test_typed_llm_survives_the_preset_spread(self, wizard_module):
        updates = run_finish_merge(wizard_module, **EXTERNAL_API_STATE)
        assert updates["LLM_URL"] == "http://typed-llm:1234/v1"
        assert updates["LLM_MODEL"] == "qwen2.5-7b-instruct"
        # typed non-empty image overrides still win over the preset
        assert updates["FLUX_API_URL"] == "http://typed-gpu:8000"
        # untouched preset values still apply
        assert updates["FLUX_DEVICE"] == "cpu"
        assert updates["FLUX_MIN_FREE_VRAM_GB"] == "0"
        assert updates["ONBOARDED"] is True

    def test_untouched_llm_fields_stay_empty_not_placeholder(self, wizard_module):
        state = {**EXTERNAL_API_STATE, "LLM_URL": "", "LLM_MODEL": ""}
        updates = run_finish_merge(wizard_module, **state)
        assert updates["LLM_URL"] == ""
        assert updates["LLM_MODEL"] == ""

    def test_typed_key_and_model_are_applied_trimmed(self, wizard_module):
        state = {**EXTERNAL_API_STATE,
                 "FLUX_API_KEY": " sk-test-123 ", "FLUX_API_MODEL": " flux-schnell "}
        updates = run_finish_merge(wizard_module, **state)
        assert updates["FLUX_API_KEY"] == "sk-test-123"
        assert updates["FLUX_API_MODEL"] == "flux-schnell"

    def test_masked_key_echo_is_dropped(self, wizard_module):
        # config_api.py masks stored secrets; echoing '********' back in the
        # wizard's PATCH would overwrite the real key with asterisks.
        state = {**EXTERNAL_API_STATE, "FLUX_API_KEY": "********", "LLM_API_KEY": "********"}
        updates = run_finish_merge(wizard_module, **state)
        assert "FLUX_API_KEY" not in updates
        assert "LLM_API_KEY" not in updates
        assert updates["FLUX_API_MODEL"] == ""  # not secret-shaped: sent as-is

    def test_typed_llm_api_key_is_applied_trimmed(self, wizard_module):
        state = {**EXTERNAL_API_STATE, "LLM_API_KEY": " sk-or-v1-test "}
        updates = run_finish_merge(wizard_module, **state)
        assert updates["LLM_API_KEY"] == "sk-or-v1-test"

    def test_masked_llm_url_credentials_are_not_echoed(self, wizard_module):
        # URL-credential masking embeds the sentinel mid-string
        state = {**EXTERNAL_API_STATE, "LLM_URL": "http://user:********@host:8080/v1"}
        updates = run_finish_merge(wizard_module, **state)
        assert "LLM_URL" not in updates

    def test_image_api_key_and_model_always_apply(self, wizard_module):
        state = {**EXTERNAL_API_STATE, "preset": "local-gpu",
                 "FLUX_API_KEY": "sk-test-123", "FLUX_API_MODEL": "my-model"}
        updates = run_finish_merge(wizard_module, **state)
        assert updates["FLUX_API_KEY"] == "sk-test-123"
        assert updates["FLUX_API_MODEL"] == "my-model"

    def test_no_preset_still_saves_typed_llm(self, wizard_module):
        state = {**EXTERNAL_API_STATE, "preset": None, "presetValues": {}}
        updates = run_finish_merge(wizard_module, **state)
        assert updates["LLM_URL"] == "http://typed-llm:1234/v1"
        assert "FLUX_DEVICE" not in updates
        assert updates["ONBOARDED"] is True

    def test_placeholder_preset_urls_are_not_persisted(self, wizard_module):
        # Leaving image URL blank used to write http://your-image-host:8000
        # from the preset into .env.
        state = {
            **EXTERNAL_API_STATE,
            "FLUX_API_URL": "",
            "LLM_URL": "http://typed-llm:1234/v1",
            "LLM_API_KEY": "sk-test",
        }
        updates = run_finish_merge(wizard_module, **state)
        assert updates["LLM_URL"] == "http://typed-llm:1234/v1"
        assert updates["FLUX_API_URL"] == ""
        assert "your-" not in updates.get("FLUX_API_URL", "")
        assert updates["FLUX_DEVICE"] == "cpu"  # non-placeholder preset value still applies

    def test_scrub_preset_values_drops_your_hosts(self, wizard_module):
        proc = subprocess.run(
            ["node", str(wizard_module), json.dumps({
                "__scrub_only": True,
                "presetValues": {
                    "LLM_URL": "http://your-llm-host:8080/",
                    "FLUX_API_URL": "http://your-image-host:8000",
                    "FLUX_DEVICE": "cpu",
                },
            })],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        scrubbed = json.loads(proc.stdout.strip().splitlines()[-1])
        assert scrubbed == {"FLUX_DEVICE": "cpu"}
