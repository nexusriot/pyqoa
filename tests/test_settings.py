import json

import keystore
from settings import DEFAULTS, PROFILE_KEYS, Settings


def test_defaults_are_loaded(settings):
    assert settings.get("model") == DEFAULTS["model"]
    assert settings.get("tools_enabled") is False


def test_save_is_atomic_and_leaves_no_temp_files(settings, data_dir):
    settings.set("model", "gpt-4o-mini")
    settings.save()
    data = json.loads((data_dir / "settings.json").read_text())
    assert data["model"] == "gpt-4o-mini"
    assert not list(data_dir.glob(".settings-*.tmp"))


def test_reload_round_trip(settings, data_dir):
    settings.set("temperature", 0.25)
    settings.save()
    assert Settings(data_dir).get("temperature") == 0.25


def test_unknown_keys_from_disk_survive(settings, data_dir):
    settings.save()
    raw = json.loads((data_dir / "settings.json").read_text())
    raw["future_option"] = 7
    (data_dir / "settings.json").write_text(json.dumps(raw))
    assert Settings(data_dir).get("future_option") == 7


def test_corrupt_settings_fall_back_to_defaults(data_dir):
    (data_dir / "settings.json").write_text("{not json")
    assert Settings(data_dir).get("model") == DEFAULTS["model"]


def test_default_profile_is_created(settings):
    assert settings.profile_names() == ["Default"]
    assert settings.active_profile == "Default"


def test_upsert_and_activate_profile(settings):
    settings.upsert_profile(
        "Ollama", {"api_url": "http://localhost:11434/v1", "model": "llama3"}
    )
    assert settings.activate_profile("Ollama")
    assert settings.get("api_url") == "http://localhost:11434/v1"
    assert settings.get("model") == "llama3"


def test_switching_profiles_keeps_each_ones_values(settings):
    settings.set("model", "gpt-4o")
    settings.upsert_profile("Local", {"api_url": "http://localhost:11434/v1",
                                      "model": "llama3"})
    settings.activate_profile("Local")
    settings.set("model", "llama3.2")          # edit while Local is active
    settings.activate_profile("Default")
    assert settings.get("model") == "gpt-4o"
    settings.activate_profile("Local")
    assert settings.get("model") == "llama3.2"


def test_activate_unknown_profile_is_refused(settings):
    assert settings.activate_profile("nope") is False


def test_rename_profile_moves_the_active_pointer(settings):
    assert settings.rename_profile("Default", "Work")
    assert settings.active_profile == "Work"
    assert settings.profile_names() == ["Work"]


def test_rename_to_existing_name_is_refused(settings):
    settings.upsert_profile("Other", {})
    assert settings.rename_profile("Default", "Other") is False


def test_last_profile_cannot_be_deleted(settings):
    assert settings.delete_profile("Default") is False


def test_delete_profile_switches_away_from_it(settings):
    settings.upsert_profile("Second", {"model": "m2"})
    settings.activate_profile("Second")
    assert settings.delete_profile("Second")
    assert settings.active_profile == "Default"


def test_profile_keys_cover_the_transport_fields():
    for key in ("api_url", "api_key", "model", "request_headers", "proxy"):
        assert key in PROFILE_KEYS


def test_keyring_scrubs_keys_from_disk(settings, data_dir, monkeypatch):
    store: dict = {}
    monkeypatch.setattr(keystore, "available", lambda: True)
    monkeypatch.setattr(keystore, "set_key",
                        lambda account, value: store.__setitem__(account, value))
    monkeypatch.setattr(keystore, "get_key", lambda account: store.get(account, ""))

    settings.set("use_keyring", True)
    settings.set("api_key", "sk-secret")
    settings.save()

    on_disk = json.loads((data_dir / "settings.json").read_text())
    assert on_disk["api_key"] == ""
    assert store[keystore.account_for("Default")] == "sk-secret"

    reloaded = Settings(data_dir)
    assert reloaded.get("api_key") == "sk-secret"
