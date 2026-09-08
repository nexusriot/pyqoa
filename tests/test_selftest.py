import sys

import selftest
import utils
from version import __version__


def test_asset_path_resolves_from_the_source_tree():
    path = utils.asset_path("icons/pyqoa.svg")
    assert path.name == "pyqoa.svg"
    assert path.parent.name == "icons"
    assert path.exists()


def test_asset_path_uses_the_pyinstaller_bundle_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert utils.asset_path("icons/pyqoa.svg") == tmp_path / "icons/pyqoa.svg"


def test_every_check_returns_a_verdict_and_a_detail(qapp):
    for name, kind, check in selftest.CHECKS:
        ok, detail = check()
        assert isinstance(ok, bool), name
        assert isinstance(detail, str) and detail, name
        assert kind in (selftest.REQUIRED, selftest.OPTIONAL)


def test_selftest_passes_in_this_environment(qapp, capsys):
    assert selftest.run() == 0
    out = capsys.readouterr().out
    assert f"PyQOA {__version__} self-test" in out
    assert "self-test passed" in out
    for name, _kind, _check in selftest.CHECKS:
        assert name in out


def test_a_failing_required_check_fails_the_run(qapp, monkeypatch, capsys):
    monkeypatch.setattr(
        selftest, "CHECKS",
        [("Broken thing", selftest.REQUIRED, lambda: (False, "nope"))],
    )
    assert selftest.run() == 1
    assert "FAIL" in capsys.readouterr().out


def test_a_raising_check_is_reported_not_propagated(qapp, monkeypatch, capsys):
    def boom():
        raise RuntimeError("exploded")

    monkeypatch.setattr(
        selftest, "CHECKS", [("Boom", selftest.REQUIRED, boom)]
    )
    assert selftest.run() == 1
    assert "RuntimeError: exploded" in capsys.readouterr().out


def test_a_failing_optional_check_only_warns(qapp, monkeypatch, capsys):
    monkeypatch.setattr(
        selftest, "CHECKS",
        [("Nice to have", selftest.OPTIONAL, lambda: (False, "absent"))],
    )
    assert selftest.run() == 0
    assert "warn" in capsys.readouterr().out


def test_selftest_is_reachable_from_the_cli():
    import main

    assert main.parse_args(["--selftest"]).selftest is True
    assert main.parse_args([]).selftest is False
