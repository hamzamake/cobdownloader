; Cob Downloader - Inno Setup script
;
; Builds "installer\Cob Downloader Setup.exe" from dist\Cob Downloader\.
; Run make-installer.bat rather than compiling this by hand: that script
; rebuilds dist\ first and passes the version read out of cob_downloader.py,
; so the installer can never advertise a version the program does not have.
;
; The payload is the frozen build, which already carries Python, yt-dlp and
; ffmpeg, so the target machine needs nothing preinstalled.

#define MyAppName    "Cob Downloader"
#define MyAppExeName "Cob Downloader.exe"
#define MyAppPublisher "Cob"
#define MyPayload    "dist\Cob Downloader"

; make-installer.bat passes this in with /D; the fallback keeps a bare
; "ISCC installer.iss" working too
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#if !FileExists(AddBackslash(SourcePath) + MyPayload + "\" + MyAppExeName)
  #error dist\Cob Downloader\ is missing - run build.bat first
#endif

[Setup]
; never change AppId: it is how Windows recognises an existing install and
; how upgrades replace it instead of piling up
AppId={{4222A79F-E0CF-4B6E-B33D-AB54AD5E4891}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}

; {autopf} follows whatever the user chose on the privileges page:
; Program Files for all-users, %LOCALAPPDATA%\Programs for just-me
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

; default to the no-UAC per-user install, but offer all-users on its own page
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=installer
OutputBaseFilename=Cob Downloader Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

; the payload is ~210 MB of mostly ffmpeg; solid LZMA2 roughly halves it
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; let Restart Manager close a running copy instead of failing on a locked exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyPayload}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[Code]
// Settings and the self-updated yt-dlp/ffmpeg live in AppData, deliberately
// outside the install folder so they survive an upgrade.  That means uninstall
// has to ask about them rather than assume - a reinstall should not silently
// lose your saved output folder or your updated yt-dlp.
// (Note: // comments, not braces - Inno treats { } as a comment delimiter and
// they do not nest, so a brace constant inside one would end it early.)
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\CobDownloader');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also delete Cob Downloader''s saved settings and its'
                + ' self-updated copies of yt-dlp and ffmpeg?' + #13#10#13#10
                + DataDir + #13#10#13#10
                + 'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
