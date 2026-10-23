import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import app

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
POSIX_SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(POSIX_SH is None, reason="no POSIX sh available")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---- Windows installer ----------------------------------------------------------------


def test_iss_declares_per_user_installer() -> None:
    iss = _read(PKG / "windows" / "ogphotos.iss")
    assert re.search(r"^AppId=\{\{[0-9A-F-]{36}\}?$", iss, re.M)
    for line in (
        "AppName=OGphotos",
        "AppVersion={#AppVersion}",
        "PrivilegesRequired=lowest",
        r"DefaultDirName={localappdata}\Programs\OGphotos",
        "OutputBaseFilename=OGphotos-Setup-{#AppVersion}",
    ):
        assert line in iss
    assert "SetupIconFile=" in iss and r"ogphotos.ico" in iss
    assert r"{userdesktop}\OGphotos" in iss and r"{userprograms}\OGphotos" in iss
    assert "Flags: postinstall nowait shellexec skipifsilent" in iss


def test_iss_is_x64_only_not_arm64() -> None:
    """x64compatible would admit Windows on ARM, where uv may pick an ARM64 Python the
    lockfile's environments don't cover."""
    lines = _read(PKG / "windows" / "ogphotos.iss").splitlines()
    assert "ArchitecturesAllowed=x64os" in lines
    assert "ArchitecturesInstallIn64BitMode=x64os" in lines
    assert not any("x64compatible" in line for line in lines if not line.startswith(";"))


def test_iss_uninstall_prompt_defaults_to_no_and_only_removes_env_folders() -> None:
    iss = _read(PKG / "windows" / "ogphotos.iss")
    assert (
        "Also remove OGphotos'' downloaded Python environment (about 1 GB)? "
        "Your photos, library and models are not touched." in iss.replace("' +\n        '", "")
    )
    assert "MB_DEFBUTTON2" in iss and "usPostUninstall" in iss
    # MsgBox is shown even under /VERYSILENT, so a silent uninstall must skip it entirely
    assert "if (not UninstallSilent) and (MsgBox(" in iss
    assert "Needs Inno Setup 6.3" in iss
    assert re.search(r'^Type: filesandordirs; Name: "\{app\}\\app"$', iss, re.M)
    trees = re.findall(r"DelTree\(([^,]+),", iss)
    assert [t.strip() for t in trees] == [
        r"Base + '\python'",
        r"Base + '\env'",
        r"Base + '\cache'",
    ]
    assert r"{localappdata}\OGphotos" in iss


def test_windows_build_script_stages_and_calls_iscc() -> None:
    ps1 = _read(PKG / "windows" / "build.ps1")
    assert "--target windows" in ps1
    assert r"Inno Setup 6\ISCC.exe" in ps1
    assert "choco install innosetup -y --no-progress" in ps1
    assert "/DAppVersion=$Version" in ps1


# ---- macOS app ------------------------------------------------------------------------


def test_info_plist_is_valid_with_version_substituted() -> None:
    raw = _read(PKG / "macos" / "Info.plist").replace("@VERSION@", app.__version__)
    info = plistlib.loads(raw.encode())
    assert info["CFBundleIdentifier"] == "dev.ogphotos.app"
    assert info["CFBundleExecutable"] == "OGphotos"
    assert info["CFBundleIconFile"] == "ogphotos"
    assert info["CFBundleShortVersionString"] == app.__version__
    assert info["LSMinimumSystemVersion"] == "14.0"


def test_macos_stub_opens_launch_sh_in_terminal() -> None:
    stub = _read(PKG / "macos" / "OGphotos")
    assert stub.startswith("#!/bin/sh\n")
    opener = 'open -a Terminal "$(dirname "$0")/../Resources/ogphotos/launch.sh"'
    unquarantine = (
        'xattr -dr com.apple.quarantine "$(dirname "$0")/../Resources/ogphotos" 2>/dev/null || true'
    )
    assert opener in stub
    assert unquarantine in stub
    # Gatekeeper would block the payload Terminal runs unless quarantine is cleared first.
    assert stub.index(unquarantine) < stub.index(opener)


def test_macos_build_script_assembles_bundle() -> None:
    sh = _read(PKG / "macos" / "build_app.sh")
    for part in (
        "--target macos",
        "Contents/MacOS/OGphotos",
        "Contents/Resources/ogphotos.icns",
        "ln -s /Applications",
        "hdiutil create -volname OGphotos",
        "-format UDZO",
        "chmod +x",
        'codesign --force --deep -s - "$APP"',
        "command -v codesign",
    ):
        assert part in sh
    # Sign the finished bundle, before it goes into the dmg.
    assert sh.index("codesign --force") > sh.index('cp -R "$WORK/stage/ogphotos"')
    assert sh.index("codesign --force") < sh.index("hdiutil create")


def test_releasing_has_manual_gatekeeper_check() -> None:
    text = _read(ROOT / "docs" / "RELEASING.md")
    assert "right-click → Open" in text
    assert "browser" in text and "Terminal" in text


# ---- Linux tarball and desktop entry ---------------------------------------------------


def test_desktop_template_fields() -> None:
    text = _read(PKG / "linux" / "ogphotos.desktop.in")
    for line in (
        "Type=Application",
        "Name=OGphotos",
        'Exec="@DIR@/launch.sh"',
        "Icon=@DIR@/icons/ogphotos.png",
        "Terminal=true",
        "Categories=Graphics;Photography;",
    ):
        assert line in text.splitlines()


def test_linux_build_script_makes_tarball() -> None:
    sh = _read(PKG / "linux" / "build_tar.sh")
    assert "--target linux" in sh
    assert 'tar -czf "$DIST/ogphotos-$VERSION-linux.tar.gz" -C "$OUT" ogphotos' in sh


@needs_sh
def test_install_desktop_substitutes_folder(tmp_path: Path) -> None:
    folder = tmp_path / "My Apps & Stuff" / "ogphotos"
    folder.mkdir(parents=True)
    for name in ("install-desktop.sh", "ogphotos.desktop.in"):
        shutil.copy2(PKG / "linux" / name, folder / name)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    subprocess.run([POSIX_SH, str(folder / "install-desktop.sh")], env=env, check=True)
    entry = (home / ".local/share/applications/ogphotos.desktop").read_text(encoding="utf-8")
    assert "@DIR@" not in entry
    exec_line = next(x for x in entry.splitlines() if x.startswith("Exec="))
    assert exec_line.endswith('/My Apps & Stuff/ogphotos/launch.sh"')
    assert any(
        x.endswith("/My Apps & Stuff/ogphotos/icons/ogphotos.png") for x in entry.splitlines()
    )


def test_launch_sh_offers_desktop_entry_only_when_interactive_on_linux() -> None:
    sh = _read(PKG / "launch.sh")
    assert "Add OGphotos to your applications menu and desktop? [Y/n]" in sh
    assert '"$HOME_DIR/.desktop-asked"' in sh
    cond = next(x for x in sh.splitlines() if x.startswith('if [ "$OS" = "linux" ]'))
    block = sh[sh.index(cond) : sh.index('exec "$PY"')]
    assert "applications/ogphotos.desktop" in sh
    for part in ("-t 0", "OGPHOTOS_NONINTERACTIVE", "DESKTOP_FILE", "install-desktop.sh"):
        assert part in block


@needs_sh
@pytest.mark.parametrize(
    "script",
    [
        "launch.sh",
        "macos/build_app.sh",
        "macos/OGphotos",
        "linux/build_tar.sh",
        "linux/install-desktop.sh",
    ],
)
def test_shell_scripts_parse(script: str) -> None:
    subprocess.run([POSIX_SH, "-n", str(PKG / script)], check=True)


@pytest.mark.parametrize(
    "script",
    ["macos/build_app.sh", "macos/OGphotos", "linux/build_tar.sh", "linux/install-desktop.sh"],
)
def test_new_shell_scripts_use_lf(script: str) -> None:
    assert b"\r" not in (PKG / script).read_bytes()


def test_launchers_do_not_write_bytecode_into_install_dir() -> None:
    assert 'set "PYTHONDONTWRITEBYTECODE=1"' in _read(PKG / "launch.cmd")
    assert "export PYTHONDONTWRITEBYTECODE=1" in _read(PKG / "launch.sh")


def test_windows_build_runs_from_repo_root() -> None:
    ps1 = _read(PKG / "windows" / "build.ps1")
    assert "Push-Location $Root" in ps1 and "Pop-Location" in ps1 and "finally" in ps1
