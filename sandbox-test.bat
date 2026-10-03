@echo off
rem Installs the built setup exe into a throwaway Windows Sandbox and runs
rem --check inside it, then shuts the sandbox down and leaves the report here
rem on the host.
rem
rem This is the only way to catch "works on my machine" bugs in the packaged
rem build: a fresh sandbox has an unpopulated Windows certificate store, no
rem Python, no yt-dlp, no ffmpeg and no JS runtime, which is exactly what a
rem person receiving the installer has.
rem
rem Only one Windows Sandbox can run at a time - close any open one first.
setlocal

cd /d "%~dp0"

if not exist "installer\Cob Downloader Setup.exe" (
    echo No installer found.  Run make-installer.bat first.
    exit /b 1
)

set OUT=%TEMP%\cob-sandbox-test
if exist "%OUT%" rd /s /q "%OUT%"
mkdir "%OUT%" 2>nul

rem this runs inside the sandbox, as its logon command
> "%OUT%\run.bat" echo @echo off
>>"%OUT%\run.bat" echo echo === sandbox test %%DATE%% %%TIME%% ^> C:\out\status.txt
>>"%OUT%\run.bat" echo "C:\setup\Cob Downloader Setup.exe" /VERYSILENT /CURRENTUSER /NORESTART /SUPPRESSMSGBOXES
>>"%OUT%\run.bat" echo echo installer exit=%%errorlevel%% ^>^> C:\out\status.txt
>>"%OUT%\run.bat" echo set APP=%%LOCALAPPDATA%%\Programs\Cob Downloader\Cob Downloader.exe
>>"%OUT%\run.bat" echo if not exist "%%APP%%" ^(echo INSTALL FAILED - exe missing ^>^> C:\out\status.txt ^& goto :done^)
>>"%OUT%\run.bat" echo "%%APP%%" --check --quiet
>>"%OUT%\run.bat" echo echo check exit=%%errorlevel%% ^>^> C:\out\status.txt
>>"%OUT%\run.bat" echo copy "%%APPDATA%%\CobDownloader\check-report.txt" C:\out\check-report.txt ^>nul 2^>^&1
>>"%OUT%\run.bat" echo :done
>>"%OUT%\run.bat" echo echo === finished === ^>^> C:\out\status.txt
>>"%OUT%\run.bat" echo shutdown /s /t 5 /f

rem .wsb needs absolute host paths, so write it fresh each run
set WSB=%OUT%\test.wsb
> "%WSB%" echo ^<Configuration^>
>>"%WSB%" echo   ^<MappedFolders^>
>>"%WSB%" echo     ^<MappedFolder^>^<HostFolder^>%~dp0installer^</HostFolder^>^<SandboxFolder^>C:\setup^</SandboxFolder^>^<ReadOnly^>true^</ReadOnly^>^</MappedFolder^>
>>"%WSB%" echo     ^<MappedFolder^>^<HostFolder^>%OUT%^</HostFolder^>^<SandboxFolder^>C:\out^</SandboxFolder^>^<ReadOnly^>false^</ReadOnly^>^</MappedFolder^>
>>"%WSB%" echo   ^</MappedFolders^>
>>"%WSB%" echo   ^<LogonCommand^>^<Command^>cmd.exe /c C:\out\run.bat^</Command^>^</LogonCommand^>
>>"%WSB%" echo   ^<Networking^>Enable^</Networking^>
>>"%WSB%" echo   ^<MemoryInMB^>4096^</MemoryInMB^>
>>"%WSB%" echo ^</Configuration^>

echo Launching Windows Sandbox.  It installs, runs --check, then shuts itself
echo down - takes a couple of minutes.  Do not close it by hand.
echo.
echo Results will be in:
echo     %OUT%\status.txt
echo     %OUT%\check-report.txt
echo.
start "" "%WSB%"
