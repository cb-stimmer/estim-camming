"""GUI helpers that don't need Qt."""

import socket

from estim_camming.config import OverlayConfig
from estim_camming.gui import model


def test_control_address_for_wildcard_and_ipv6():
    assert model.api_base(OverlayConfig(port=9000)) == "http://127.0.0.1:9000"
    wildcard = OverlayConfig(host="0.0.0.0", port=9001, control_token="0123456789abcdef")
    assert model.api_base(wildcard) == "http://127.0.0.1:9001"
    assert model.api_base(OverlayConfig(host="::1", port=9002)) == "http://[::1]:9002"


def test_port_in_use():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert model.port_in_use("127.0.0.1", port)
    assert not model.port_in_use("127.0.0.1", port)


def test_formatting():
    action = {
        "label": "Special",
        "outputs": [
            {"channel": "A", "pattern": "pulse", "intensity": 0.8},
            {"channel": "B", "pattern": "wave", "intensity": 0.5},
        ],
        "tip": {"username": "bob"},
    }
    assert model.format_outputs(action) == "A: pulse 80% · B: wave 50%"
    assert model.queue_text(action) == "Special  (bob)"
    assert model.queue_text({"label": "X", "tip": None}) == "X"
    assert [model.format_seconds(s) for s in (-1, 4.4, 59.6, 61, 600)] == [
        "0s",
        "4s",
        "1:00",
        "1:01",
        "10:00",
    ]
    assert model.token_range({"min_tokens": 1, "max_tokens": None}) == "1+"
    assert model.token_range({"min_tokens": 99, "max_tokens": 99}) == "99"
    tip = {"username": "eve", "tokens": 25, "platform": "stripchat"}
    assert model.tip_text(tip) == "eve  25 tk  (stripchat)"
    assert model.platform_text("cb", {"connected": False, "detail": "HTTP 500"}) == (
        "cb: DISCONNECTED: HTTP 500"
    )


def test_desktop_entry_quotes_paths_for_the_exec_key(tmp_path):
    config = tmp_path / "my show" / "config.toml"
    config.parent.mkdir()
    config.write_text("")
    entry = model.desktop_entry(config, python="/venv/bin/python")
    lines = dict(line.split("=", 1) for line in entry.splitlines()[1:])
    assert lines["Exec"] == (f'/venv/bin/python -m estim_camming gui -c "{config.resolve()}"')
    assert lines["Icon"] == lines["StartupWMClass"] == model.APP_ID
    assert lines["Path"] == str(config.parent.resolve())


def test_exec_arg_escaping():
    assert model._exec_arg("plain/path") == "plain/path"
    assert model._exec_arg("50%") == "50%%"
    assert model._exec_arg('a"b$c') == '"a\\\\"b\\\\$c"'


def test_icon_is_packaged():
    assert model.icon_path().is_file()
    assert model.icon_path().read_text().startswith("<svg")
