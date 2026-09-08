"""Checks over the build scripts, Makefile and packaging metadata.

These are cheap guards against the packaging drifting away from the code: a
renamed CLI flag that the man page never learned about, a Makefile target that
lost its .PHONY, a script with a syntax error that only shows up in CI.
"""

import re
import subprocess
from pathlib import Path

import pytest

from version import __version__

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
SCRIPTS = sorted(PACKAGING.glob("*.sh"))
MAKEFILE = (ROOT / "Makefile").read_text()

EXPECTED_TARGETS = [
    "help", "version", "venv", "deps", "deps-core", "deps-dev", "deps-build", "run",
    "test", "test-cov", "lint", "selftest", "check", "binary", "onefile",
    "verify-binary", "tarball", "deb", "deb-lint", "sdist", "install",
    "uninstall", "clean", "distclean",
]


def test_the_expected_scripts_exist():
    assert {p.name for p in SCRIPTS} == {
        "build-binary.sh", "build-deb.sh", "build-tarball.sh", "install-tree.sh"
    }


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_are_executable_and_parse(script):
    assert script.stat().st_mode & 0o111, "not executable"
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_fail_fast(script):
    assert "set -euo pipefail" in script.read_text()


@pytest.mark.parametrize("target", EXPECTED_TARGETS)
def test_makefile_declares_the_target(target):
    assert re.search(rf"^{re.escape(target)}:", MAKEFILE, re.MULTILINE)


def test_every_makefile_target_is_phony():
    declared = set(re.findall(r"^([a-z][a-z0-9-]*):", MAKEFILE, re.MULTILINE))
    phony = set(re.findall(r"[ \t]([a-z][a-z0-9-]*)", MAKEFILE.split(".PHONY:")[1]
                           .split("\n\n")[0]))
    assert declared - phony == set(), f"missing from .PHONY: {declared - phony}"


def test_makefile_help_lists_documented_targets():
    documented = set(re.findall(r"^([a-z][a-z0-9-]*):.*## ", MAKEFILE, re.MULTILINE))
    assert set(EXPECTED_TARGETS) - documented == set()


def test_makefile_runs_qt_headless_for_tests():
    assert "QT_QPA_PLATFORM ?= offscreen" in MAKEFILE


def test_desktop_entry_is_well_formed():
    text = (PACKAGING / "pyqoa.desktop").read_text()
    entries = dict(
        line.split("=", 1) for line in text.splitlines()
        if "=" in line and not line.startswith("[")
    )
    assert entries["Type"] == "Application"
    assert entries["Exec"].startswith("pyqoa")
    assert entries["Icon"] == "pyqoa"
    # Exactly one main category, or the app shows up twice in menus.
    mains = {
        "AudioVideo", "Development", "Education", "Game", "Graphics", "Network",
        "Office", "Science", "Settings", "System", "Utility",
    }
    categories = [c for c in entries["Categories"].split(";") if c]
    assert len(mains.intersection(categories)) == 1


def test_icon_is_a_square_svg():
    text = (PACKAGING / "icons" / "pyqoa.svg").read_text()
    assert text.lstrip().startswith("<?xml")
    assert 'viewBox="0 0 256 256"' in text


def test_pyinstaller_spec_points_at_files_that_exist():
    spec = (PACKAGING / "pyqoa.spec").read_text()
    assert '"icons" / "pyqoa.svg"' in spec
    assert (PACKAGING / "icons" / "pyqoa.svg").exists()
    # chromadb is deliberately excluded; keep that decision visible.
    assert '"chromadb",' in spec


def test_man_page_version_matches_the_code():
    man = (PACKAGING / "pyqoa.1").read_text()
    assert f'"PyQOA {__version__}"' in man


def test_man_page_documents_every_cli_flag():
    import main

    man = (PACKAGING / "pyqoa.1").read_text()
    flags = [
        option
        for action in main.build_parser()._actions
        for option in action.option_strings
        if option.startswith("--")
    ]
    # roff escapes the hyphens, so accept either spelling.
    missing = [
        flag for flag in flags
        if flag not in man and flag.replace("-", "\\-") not in man
    ]
    assert missing == [], f"undocumented flags: {missing}"


def test_readme_and_deb_agree_with_the_version():
    readme = (ROOT / "README.md").read_text()
    assert f"**{__version__}**" in readme
    deb = (PACKAGING / "build-deb.sh").read_text()
    # The .deb version comes from version.py rather than a second literal.
    assert "from version import __version__" in deb


def _workflow():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


def test_ci_workflow_is_valid_and_uses_the_make_targets():
    data = _workflow()
    assert set(data["jobs"]) == {"test", "test-minimal", "package"}
    commands = " ".join(
        step.get("run", "")
        for job in data["jobs"].values()
        for step in job["steps"]
    )
    for target in ("make check", "make deb", "make verify-binary"):
        assert target in commands


def test_ci_packages_only_after_the_tests_pass():
    assert set(_workflow()["jobs"]["package"]["needs"]) == {"test", "test-minimal"}


def test_ci_jobs_are_time_bounded():
    for name, job in _workflow()["jobs"].items():
        assert job.get("timeout-minutes"), f"{name} has no timeout"


def test_ci_installs_the_requirements_files_that_exist():
    data = _workflow()
    commands = " ".join(
        step.get("run", "")
        for job in data["jobs"].values()
        for step in job["steps"]
    )
    for name in re.findall(r"requirements[a-z-]*\.txt", commands):
        assert (ROOT / name).exists(), name


def test_ci_lintian_gate_actually_fails_the_build():
    steps = _workflow()["jobs"]["package"]["steps"]
    # The step that *invokes* lintian, not the apt step that installs it.
    lintian = next(
        s for s in steps if s.get("run", "").strip().startswith("lintian")
    )
    # Without --fail-on, lintian reports tags and still exits 0.
    assert "--fail-on" in lintian["run"]


def test_docs_state_the_real_test_count(request):
    """Keep the counts quoted in README.md and DESIGN.md honest."""
    option = request.config.option
    selected_paths = option.file_or_dir and option.file_or_dir != ["tests"]
    if option.keyword or option.markexpr or selected_paths:
        pytest.skip("a filtered run cannot know the full test count")
    count = request.session.testscollected
    for name in ("README.md", "DESIGN.md"):
        text = (ROOT / name).read_text()
        assert f"{count} tests" in text, (
            f"{name} does not mention '{count} tests' — update it"
        )


def test_requirements_split_keeps_the_optional_extras_separate():
    core = (ROOT / "requirements-core.txt").read_text()
    full = (ROOT / "requirements.txt").read_text()
    assert "-r requirements-core.txt" in full
    for required in ("PyQt6", "openai"):
        assert required in core
    for optional in ("chromadb", "tiktoken", "keyring"):
        assert optional not in core, f"{optional} is optional, not core"
        assert optional in full
