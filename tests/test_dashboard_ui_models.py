"""Browser tests for Settings -> AI Models: the backend/model compatibility warning.

Everything here is deliberately what the pytest suites cannot reach. `tests/
test_dashboard_server_routes.py` already pins the server's verdict on every backend/model
pair; what it cannot see is whether the *page* tells the user before they hit Save, whether
the warning survives a reload of an already-broken config.json, and whether the save-bar
error actually reaches the screen. Those are the three ways the original report's user was
left in the dark, so they are what is asserted here.

Run with `python -m pytest -m browser` (needs `python -m playwright install chromium`).
"""

from __future__ import annotations

import pytest

import tempa_config

pytestmark = pytest.mark.browser

STAGES = ("clarify", "clarify_apply", "plan", "implement")


def _open_ai_models_tab(page, url):
    page.goto(url)
    # The sidebar is rendered by the page's own JS (renderSidebar), so the Settings item
    # only exists once that has run -- and clicking it is what selectTop("settings") wires
    # up, which is the path a user takes.
    page.locator("#treeBottom").get_by_text("Settings", exact=True).click()
    page.click("#settingsTabModelsBtn")
    page.wait_for_selector("#settingsBackendClarify", state="visible")


CUSTOM = "__custom__"


def _model_options(page):
    return page.locator("#settingsModelSelectClarify option").evaluate_all(
        "els => els.map(e => e.value)")


def _set_stage(page, backend, model):
    """Point the Clarifications row at one backend/model pair the way a user would: pick the
    model from the list when it is offered there, otherwise switch to Custom and type it."""
    page.select_option("#settingsBackendClarify", backend)
    if model in _model_options(page):
        page.select_option("#settingsModelSelectClarify", model)
    else:
        page.select_option("#settingsModelSelectClarify", CUSTOM)
        page.fill("#settingsModelClarify", model)
    # `fill` and `select_option` dispatch the events the note listens on; settle before
    # asserting on what they produced.
    page.wait_for_timeout(50)


def _note(page):
    return page.locator("#settingsModelNoteClarify")


def test_mismatched_pair_warns_inline_before_saving(page, dashboard_server):
    """The reported bug, from the user's side: backend moved to Codex, model still
    Anthropic's. Previously the page said nothing at all and the failure only surfaced
    mid-run, in a log file."""
    _open_ai_models_tab(page, dashboard_server)
    _set_stage(page, "codex", "claude-sonnet-5")

    note = _note(page)
    assert note.is_visible()
    assert "warn" in (note.get_attribute("class") or "")
    text = note.inner_text()
    assert "Anthropic" in text
    assert "OpenAI Codex CLI" in text


def test_saving_a_mismatched_pair_is_refused_and_writes_nothing(page, dashboard_server):
    _open_ai_models_tab(page, dashboard_server)
    before = tempa_config.load_config().get("models", {}).get("clarify")
    _set_stage(page, "codex", "claude-sonnet-5")

    page.click("#settingsSaveBtn")
    status = page.locator("#settingsSaveStatus")
    status.wait_for(state="visible")
    page.wait_for_function(
        "() => document.getElementById('settingsSaveStatus').textContent.trim().length > 0")
    assert "cannot run on the selected backend" in status.inner_text()
    assert "err" in (status.get_attribute("class") or "")
    assert tempa_config.load_config().get("models", {}).get("clarify") == before


def test_an_anthropic_model_on_copilot_is_accepted(page, dashboard_server):
    """Copilot proxies several providers, so this pair is valid. The check must not break a
    configuration that works today."""
    _open_ai_models_tab(page, dashboard_server)
    _set_stage(page, "copilot", "claude-sonnet-5")

    note = _note(page)
    assert "warn" not in (note.get_attribute("class") or "")

    page.click("#settingsSaveBtn")
    page.wait_for_function(
        "() => document.getElementById('settingsSaveStatus').textContent.trim().length > 0")
    saved = tempa_config.load_config()
    assert saved["backends"]["clarify"] == "copilot"
    assert saved["models"]["clarify"] == "claude-sonnet-5"


def test_a_model_id_from_no_known_vendor_is_accepted(page, dashboard_server):
    """Free text stays free text: an id the vendor table cannot place is nobody's to
    reject, so a private or future model still saves."""
    _open_ai_models_tab(page, dashboard_server)
    _set_stage(page, "codex", "some-internal-model")

    assert "warn" not in (_note(page).get_attribute("class") or "")

    page.click("#settingsSaveBtn")
    page.wait_for_function(
        "() => document.getElementById('settingsSaveStatus').textContent.trim().length > 0")
    assert tempa_config.load_config()["models"]["clarify"] == "some-internal-model"


def test_a_mismatch_already_in_config_json_warns_on_load(page, dashboard_server):
    """Regression for the ordering bug this change had to fix: fillSettingsForm used to
    compute the note before populating the model inputs, so a pair already saved as broken
    showed nothing until the user happened to touch a field."""
    config = tempa_config.load_config()
    config["backends"] = {**config.get("backends", {}), "clarify": "codex"}
    config["models"] = {**config.get("models", {}), "clarify": "claude-sonnet-5"}
    tempa_config.save_config(config)

    _open_ai_models_tab(page, dashboard_server)

    note = _note(page)
    assert note.is_visible()
    assert "warn" in (note.get_attribute("class") or "")


def test_fixing_the_pair_clears_the_warning(page, dashboard_server):
    _open_ai_models_tab(page, dashboard_server)
    _set_stage(page, "codex", "claude-sonnet-5")
    assert "warn" in (_note(page).get_attribute("class") or "")

    _set_stage(page, "codex", "gpt-5.6-sol")
    assert "warn" not in (_note(page).get_attribute("class") or "")


# ---------------------------------------------------------------------------
# The model picker itself
# ---------------------------------------------------------------------------

def test_the_picker_offers_only_models_the_backend_serves_plus_custom(page, dashboard_server):
    """The reason the picker exists: a text input with a <datalist> had no visible
    affordance, so a user who did not already know an id had nothing to go on."""
    _open_ai_models_tab(page, dashboard_server)

    page.select_option("#settingsBackendClarify", "codex")
    codex = _model_options(page)
    assert "gpt-5.6-sol" in codex
    assert "claude-sonnet-5" not in codex
    assert codex[-1] == CUSTOM, "Custom must be reachable, and last, on every backend"

    page.select_option("#settingsBackendClarify", "claude")
    claude = _model_options(page)
    assert "claude-sonnet-5" in claude
    assert "gpt-5.6-sol" not in claude

    # Copilot proxies both vendors, so its list is the union.
    page.select_option("#settingsBackendClarify", "copilot")
    copilot = _model_options(page)
    assert {"claude-sonnet-5", "gpt-5.6-sol", "auto"} <= set(copilot)


def test_the_custom_field_is_hidden_until_custom_is_chosen(page, dashboard_server):
    _open_ai_models_tab(page, dashboard_server)
    page.select_option("#settingsBackendClarify", "claude")
    page.select_option("#settingsModelSelectClarify", "claude-sonnet-5")
    assert page.locator("#settingsModelClarify").is_hidden()

    page.select_option("#settingsModelSelectClarify", CUSTOM)
    assert page.locator("#settingsModelClarify").is_visible()


def test_choosing_from_the_list_is_what_gets_saved(page, dashboard_server):
    """The <select> writes through to the text input, which is what buildSettingsPayload
    reads — so picking, with no typing at all, has to reach config.json."""
    _open_ai_models_tab(page, dashboard_server)
    page.select_option("#settingsBackendClarify", "claude")
    page.select_option("#settingsModelSelectClarify", "claude-opus-5")

    page.click("#settingsSaveBtn")
    page.wait_for_function(
        "() => document.getElementById('settingsSaveStatus').textContent.trim().length > 0")
    assert tempa_config.load_config()["models"]["clarify"] == "claude-opus-5"


def test_a_model_id_the_catalog_does_not_list_loads_as_custom(page, dashboard_server):
    """A stale catalog must not become a wall. An id set from the CLI, hand-edited in, or
    simply newer than this list has to come back visible and editable, not silently
    replaced by whatever happened to be first in the dropdown."""
    config = tempa_config.load_config()
    config["backends"] = {**config.get("backends", {}), "clarify": "codex"}
    config["models"] = {**config.get("models", {}), "clarify": "gpt-6-unreleased"}
    tempa_config.save_config(config)

    _open_ai_models_tab(page, dashboard_server)

    assert page.locator("#settingsModelSelectClarify").input_value() == CUSTOM
    assert page.locator("#settingsModelClarify").is_visible()
    assert page.locator("#settingsModelClarify").input_value() == "gpt-6-unreleased"
    # Unplaceable vendor, so no mismatch warning either — free text stays free text.
    assert "warn" not in (_note(page).get_attribute("class") or "")


def test_switching_backend_keeps_the_model_instead_of_clobbering_it(page, dashboard_server):
    """Long-standing deliberate behaviour (see wireBackendModelStage): switching backends
    never discards the configured model. With a picker that means falling through to
    Custom, with the mismatch note — not a silent reset — explaining the problem."""
    _open_ai_models_tab(page, dashboard_server)
    page.select_option("#settingsBackendClarify", "claude")
    page.select_option("#settingsModelSelectClarify", "claude-sonnet-5")

    page.select_option("#settingsBackendClarify", "codex")

    assert page.locator("#settingsModelSelectClarify").input_value() == CUSTOM
    assert page.locator("#settingsModelClarify").input_value() == "claude-sonnet-5"
    assert "warn" in (_note(page).get_attribute("class") or "")
