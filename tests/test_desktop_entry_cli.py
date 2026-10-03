"""`estim-camming desktop-entry` installs and removes the launcher entry."""

from importlib.resources import files

from estim_camming.cli import main


def test_install_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    config = tmp_path / "config.toml"
    config.write_text((files("estim_camming") / "example_config.toml").read_text())
    entry = tmp_path / "share" / "applications" / "estim-camming.desktop"
    icon = tmp_path / "share" / "icons" / "hicolor" / "scalable" / "apps" / "estim-camming.svg"

    assert main(["desktop-entry", "-c", str(config)]) == 0
    assert "Exec=" in entry.read_text() and icon.read_text().startswith("<svg")
    assert f"\nIcon={icon}\n" in entry.read_text()

    assert main(["desktop-entry", "--remove"]) == 0
    assert not entry.exists() and not icon.exists()


def test_refuses_missing_or_broken_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert main(["desktop-entry", "-c", str(tmp_path / "nope.toml")]) == 2
    broken = tmp_path / "broken.toml"
    broken.write_text("[device]\ntype = 'dummy'\n")  # no rules
    assert main(["desktop-entry", "-c", str(broken)]) == 2
    assert not (tmp_path / "share" / "applications").exists()
