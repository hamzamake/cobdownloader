@echo off
rem Builds installer\Cob Downloader Setup.exe
rem
rem Rebuilds dist\ first so the installer can never ship a stale program,
rem then compiles installer.iss with the version read out of cob_downloader.py.
rem Pass /nobuild to skip the rebuild and package whatever is in dist\ already.
setlocal

cd /d "%~dp0"

set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
    echo Inno Setup 6 was not found.  Install it with:
    echo     winget install --id JRSoftware.InnoSetup
    exit /b 1
)

if /i "%~1"=="/nobuild" goto :package
echo === rebuilding dist\ ===
call "%~dp0build.bat" /nopause
if errorlevel 1 (
    echo build.bat failed - not packaging a broken build.
    exit /b 1
)

:package
rem single source of truth: APP_VERSION in cob_downloader.py.  Written to a
rem file and read back, because quoting a python one-liner inside for /f
rem backquotes is more trouble than it is worth in cmd.
set VERFILE=%TEMP%\cob-version.txt
python -c "import io;print(next(l for l in io.open('cob_downloader.py',encoding='utf-8') if l.startswith('APP_VERSION')).split('=')[1].strip().strip(chr(34)))" > "%VERFILE%"
if errorlevel 1 (
    echo Could not read APP_VERSION from cob_downloader.py
    exit /b 1
)
set VER=
set /p VER=<"%VERFILE%"
del "%VERFILE%" >nul 2>&1
if "%VER%"=="" (
    echo APP_VERSION in cob_downloader.py looks empty.
    exit /b 1
)

echo === compiling installer for version %VER% ===
"%ISCC%" /DMyAppVersion=%VER% "installer.iss"
if errorlevel 1 (
    echo Inno Setup failed.
    exit /b 1
)

echo.
echo Built installer\Cob Downloader Setup.exe  (version %VER%)
echo That single file is what you hand to someone else.
if /i not "%~1"=="/nopause" pause
