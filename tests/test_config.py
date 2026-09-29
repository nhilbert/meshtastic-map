from meshplay.config import PROJECT_ROOT, load_settings


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("MESHTASTIC_PORT", "COM99")
    monkeypatch.setenv("MESHPLAY_DATA_DIR", "somewhere")
    s = load_settings()
    assert s.port == "COM99"
    assert s.data_dir == PROJECT_ROOT / "somewhere"
