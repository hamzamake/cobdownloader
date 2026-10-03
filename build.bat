@echo off
rem Rebuilds dist\Cob Downloader\ from cob_downloader.py.
rem Pass /nopause when calling this from another script (make-installer.bat).
setlocal

cd /d "%~dp0"

set FF=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-7.1.1-essentials_build\bin
if not exist "vendor\qjs.exe" (
    echo Missing vendor\qjs.exe - the bundled JS runtime YouTube needs.
    echo Download qjs-windows-x86_64.exe from
    echo     https://github.com/quickjs-ng/quickjs/releases
    echo and save it as vendor\qjs.exe   ^(see vendor\README.txt^)
    exit /b 1
)

if not exist "%FF%\ffmpeg.exe" (
    echo Cannot find ffmpeg at:
    echo     %FF%
    echo Install it with:  winget install --id Gyan.FFmpeg.Essentials
    exit /b 1
)

rem a running copy holds a lock on dist\ and makes PyInstaller fail with
rem "PermissionError: [WinError 5] Access is denied"
taskkill /im "Cob Downloader.exe" /f >nul 2>&1

python -m PyInstaller --noconfirm --clean --windowed --noupx ^
  --name "Cob Downloader" ^
  --icon icon.ico ^
  --add-data "icon.ico;." ^
  --add-binary "%FF%\ffmpeg.exe;." ^
  --add-binary "%FF%\ffprobe.exe;." ^
  --add-binary "vendor\qjs.exe;." ^
  --add-data "vendor\LICENSE-quickjs-ng.txt;." ^
  --collect-submodules yt_dlp ^
  --collect-all charset_normalizer ^
  --collect-all chardet ^
  cob_downloader.py
if errorlevel 1 (
    echo.
    echo PyInstaller failed.
    exit /b 1
)

echo.
echo Built dist\Cob Downloader\ - run make-installer.bat to wrap it in a setup exe.
if /i not "%~1"=="/nopause" pause
