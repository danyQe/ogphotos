"""Invariants of the GitHub Actions workflows (they can't run locally, so pin the shape)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
OSES = {"ubuntu-latest", "windows-latest", "macos-14"}


def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # YAML 1.1 reads the bare key `on` as True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def runs(job: dict[str, Any]) -> list[str]:
    return [step.get("run", "") for step in job["steps"]]


def uses(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [s for s in job["steps"] if s.get("uses", "").split("@")[0] == action]


def matrix_oses(job: dict[str, Any]) -> set[str]:
    matrix = job["strategy"]["matrix"]
    if "include" in matrix:
        return {entry["os"] for entry in matrix["include"]}
    return set(matrix["os"])


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return load("ci.yml")


def test_ci_triggers(ci: dict[str, Any]) -> None:
    assert "pull_request" in ci["on"]
    assert ci["on"]["push"]["branches"] == ["main"]


def test_ci_runs_on_three_oses(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["test"]
    assert matrix_oses(job) == OSES
    assert job["strategy"]["fail-fast"] is False


def test_ci_pins_uv_and_python(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["test"]
    (setup,) = uses(job, "astral-sh/setup-uv")
    assert setup["uses"] == "astral-sh/setup-uv@v6"
    assert setup["with"]["python-version"] == "3.12"
    assert "packaging/uv-version.txt" in "\n".join(runs(job))
    assert "steps.uv.outputs.version" in setup["with"]["version"]


def test_ci_checks(ci: dict[str, Any]) -> None:
    commands = runs(ci["jobs"]["test"])
    for expected in (
        "uv sync",
        "uv lock --check",
        "uv run ruff check .",
        "uv run ruff format --check app tests scripts packaging",
        "uv run pytest",
    ):
        assert expected in commands
    assert uses(ci["jobs"]["test"], "actions/checkout")[0]["uses"] == "actions/checkout@v4"


def test_ci_third_party_check_is_ubuntu_only(ci: dict[str, Any]) -> None:
    (step,) = [s for s in ci["jobs"]["test"]["steps"] if "gen_third_party" in s.get("run", "")]
    assert step["run"] == "uv run python scripts/gen_third_party.py --check"
    assert step["if"] == "runner.os == 'Linux'"


@pytest.fixture(scope="module")
def release() -> dict[str, Any]:
    return load("release.yml")


def test_release_triggers(release: dict[str, Any]) -> None:
    assert release["on"]["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in release["on"]


def test_release_version_job_checks_tag_pyproject_and_app(release: dict[str, Any]) -> None:
    script = "\n".join(runs(release["jobs"]["version"]))
    for needle in ("GITHUB_REF_NAME#v", "pyproject.toml", "app/__init__.py", "CHANGELOG.md"):
        assert needle in script
    # The early CHANGELOG check is anchored like the release job's extraction.
    assert "index($0, head) == 1" in script
    assert "version" in release["jobs"]["version"]["outputs"]


@pytest.mark.parametrize("job", ["build", "smoke"])
def test_release_matrix(release: dict[str, Any], job: str) -> None:
    assert matrix_oses(release["jobs"][job]) == OSES
    assert release["jobs"][job]["strategy"]["fail-fast"] is False


def test_release_build_uses_packaging_scripts(release: dict[str, Any]) -> None:
    job = release["jobs"]["build"]
    script = "\n".join(runs(job))
    for builder in (
        "packaging/windows/build.ps1",
        "packaging/macos/build_app.sh",
        "packaging/linux/build_tar.sh",
    ):
        assert builder in script
    (setup,) = uses(job, "astral-sh/setup-uv")
    assert setup["with"]["python-version"] == "3.12"
    assert setup["with"]["enable-cache"] is True
    assert uses(job, "actions/upload-artifact")[0]["uses"] == "actions/upload-artifact@v4"


def test_release_smoke_env_and_checks(release: dict[str, Any]) -> None:
    job = release["jobs"]["smoke"]
    assert set(job["needs"]) >= {"version", "build"}
    script = "\n".join(runs(job))
    for needle in (
        "OGPHOTOS_NO_BROWSER",
        "OGPHOTOS_NONINTERACTIVE",
        "OGPHOTOS_HOME",
        "OGPHOTOS_CONFIG_FILE",
        "OGPHOTOS_DATA_DIR",
        "RUNNER_TEMP",
        "127.0.0.1:$port/healthz",
        "port=8000",
        "run/instance.json",
        "run\\instance.json",
        "LOCALAPPDATA",
        ".smoke-keep",
        "::error::uninstaller still running after 3 min",
        "/VERYSILENT /CURRENTUSER",
        "hdiutil attach",
        "Contents/Resources/ogphotos/launch.sh",
        "tar -xzf",
    ):
        assert needle in script, needle
    (log,) = uses(job, "actions/upload-artifact")
    assert log["if"] == "failure()"
    assert "logs/ogphotos.log" in log["with"]["path"]


def test_release_smoke_downloads_a_model_file_in_the_installed_env(
    release: dict[str, Any],
) -> None:
    steps = release["jobs"]["smoke"]["steps"]
    names = [s.get("name", "") for s in steps]
    (step,) = [s for s in steps if "installed Python" in s.get("name", "")]
    assert "if" not in step  # every OS
    assert step["shell"] == "bash"
    for needle in (
        "env/Scripts/python.exe",
        "env/bin/python",
        'PYTHONPATH="$payload"',
        "from app.services.model_install import",
        "install_model(",
        "1_Pooling/config.json",
        "EMBED_MODEL_ID",
        "assert got.is_file()",
    ):
        assert needle in step["run"], needle
    index = names.index(step["name"])
    # After both /healthz checks (the env exists), before Windows uninstalls the payload.
    assert index > names.index("Launch and wait for /healthz (Windows)")
    assert index > names.index("Launch and wait for /healthz (macOS, Linux)")
    assert index < names.index("Uninstall keeps the Python environment (Windows)")


def test_release_job(release: dict[str, Any]) -> None:
    job = release["jobs"]["release"]
    assert "smoke" in job["needs"]
    assert job["permissions"] == {"contents": "write"}
    assert "refs/tags/v" in job["if"]
    script = "\n".join(runs(job))
    assert "SHA256SUMS.txt" in script
    assert "CHANGELOG.md" in script
    assert "gh release create" in script and "--draft" in script
    assert uses(job, "actions/download-artifact")[0]["uses"] == "actions/download-artifact@v4"


def test_only_release_job_can_write(release: dict[str, Any]) -> None:
    assert release["permissions"] == {"contents": "read"}
    writers = [name for name, job in release["jobs"].items() if "permissions" in job]
    assert writers == ["release"]
