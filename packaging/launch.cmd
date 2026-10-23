@echo off
rem OGphotos launcher for Windows.
rem
rem The download ships the app source, uv.lock and a pinned uv binary. The first launch (and
rem the first after an upgrade changes uv.lock) builds the Python environment with
rem "uv sync --frozen --no-dev" in the per-user app folder; later launches skip uv entirely
rem and start the environment's Python directly, so they make no network calls.
rem
rem Two double-clicks during the first-run sync are safe without a lock here: uv holds its
rem own lock on the environment, and app.launcher takes a single-instance lock once started.
rem
rem Install paths may contain spaces and parentheses (e.g. "C:\Program Files (x86)"), so
rem every path is quoted and there are no parenthesised if/else blocks: an expanded ")"
rem would end such a block early. Control flow uses goto labels instead.

setlocal EnableExtensions DisableDelayedExpansion

set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

set "HOME_DIR=%LOCALAPPDATA%\OGphotos"
if defined OGPHOTOS_HOME set "HOME_DIR=%OGPHOTOS_HOME%"
set "OGPHOTOS_HOME=%HOME_DIR%"

set "UV=%HERE%\bin\x86_64\uv.exe"
if not exist "%UV%" goto :no_uv

set "UV_PYTHON_INSTALL_DIR=%HOME_DIR%\python"
set "UV_PROJECT_ENVIRONMENT=%HOME_DIR%\env"
set "UV_CACHE_DIR=%HOME_DIR%\cache"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_PYTHON=3.12"
set "PYTHONDONTWRITEBYTECODE=1"
rem The project is virtual (not installed into the env), so Python finds "app" here.
set "PYTHONPATH=%HERE%"

set "PY=%HOME_DIR%\env\Scripts\python.exe"
set "MARKER=%HOME_DIR%\env\.lock-hash"

rem The banner and hint contain non-ASCII text; this file is UTF-8, so those lines are
rem read and echoed under code page 65001, then the user's code page is restored.
set "OLDCP="
for /f "tokens=2 delims=:" %%c in ('chcp') do set "OLDCP=%%c"
if defined OLDCP set "OLDCP=%OLDCP: =%"
if defined OLDCP set "OLDCP=%OLDCP:.=%"

rem certutil prints the hash on its second line (older Windows puts spaces between bytes).
set "HASH="
pushd "%HERE%"
for /f "skip=1 delims=" %%h in ('certutil -hashfile uv.lock SHA256') do if not defined HASH set "HASH=%%h"
popd
if defined HASH set "HASH=%HASH: =%"

set "OLD="
if exist "%MARKER%" set /p OLD=<"%MARKER%"

if not defined HASH goto :sync
if not "%HASH%"=="%OLD%" goto :sync
if not exist "%PY%" goto :sync
goto :run

:sync
chcp 65001 >nul
echo Setting up OGphotos (one-time download of about 1 GB; usually 5–30 min)…
if defined OLDCP chcp %OLDCP% >nul
if not exist "%HOME_DIR%" mkdir "%HOME_DIR%"
"%UV%" sync --frozen --no-dev --project "%HERE%"
if errorlevel 1 goto :sync_failed
if defined HASH >"%MARKER%" echo %HASH%

:run
rem "python -m" puts the current folder first on sys.path; run from here so an unrelated
rem "app" folder in the caller's directory can't shadow ours (setlocal restores the cwd).
cd /d "%HERE%"
"%PY%" -m app.launcher %*
set "RC=%errorlevel%"
rem 0 = normal stop, 1 = the launcher already reported the error and paused. Negative codes
rem (e.g. STATUS_CONTROL_C_EXIT when the window gets Ctrl+C) aren't a broken env either.
rem GEQ compares as signed numbers, so only codes >= 2 show the repair hint. (Test RC, not
rem "if errorlevel": in a .cmd file the "set" above has already reset ERRORLEVEL to 0.)
if %RC% GEQ 2 goto :start_failed
exit /b %RC%

:start_failed
echo OGphotos couldn't start. Delete the folder "%HOME_DIR%\env" and run again to repair.
if not "%OGPHOTOS_NONINTERACTIVE%"=="1" pause
exit /b %RC%

:sync_failed
echo.
echo OGphotos setup failed. Please check your internet connection and re-run OGphotos.
goto :wait_fail

:no_uv
echo OGphotos: can't find "%UV%". Please download OGphotos again.

:wait_fail
if not "%OGPHOTOS_NONINTERACTIVE%"=="1" pause
exit /b 1
