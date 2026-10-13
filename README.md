# Cob Downloader

A tkinter GUI for yt-dlp: paste one link or a whole list of them, pick the file
type, resolution and frame rate, and choose whether you get the single video you
linked or the whole playlist.

## Share it

`installer\Cob Downloader Setup.exe` (66 MB) is what you send to someone on
Windows - download it from this repo's Releases page, or build it yourself. It offers a per-user install (no admin) or all-users, makes a Start
Menu shortcut, and registers an uninstaller in Add/Remove Programs. Nothing
needs to be on their machine first - Python, yt-dlp, ffmpeg and a JS runtime
are all inside. Windows SmartScreen will warn once because the exe is not
code-signed: More info -> Run anyway.

Rebuild it with `make-installer.bat` (needs `pip install pyinstaller` and Inno
Setup 6), which runs `build.bat` first. `sandbox-test.bat` then installs the
result into a throwaway Windows Sandbox and runs the self check inside it,
which is the only honest way to see what a clean machine gets.

Uninstalling leaves `%APPDATA%\CobDownloader\` alone unless you say otherwise,
so your settings and any self-updated yt-dlp/ffmpeg survive a reinstall.

### On a Mac

There are two disk images, one per kind of Mac: `Cob Downloader (Apple
Silicon).dmg` for M1 and newer, `Cob Downloader (Intel).dmg` for older ones
(Apple menu -> About This Mac says which). Open it and drag the app onto Applications.
Like the Windows build, everything it needs is inside.

The app is not signed with a paid Apple Developer ID, so the first launch is
blocked with "Apple could not verify...". Click Done, then open System Settings
-> Privacy & Security, scroll down and click **Open Anyway**. That is needed
only once. (Or, in Terminal: `xattr -dr com.apple.quarantine
"/Applications/Cob Downloader.app"`.)

Building needs a Mac: `bash build-mac.sh` produces the image for that Mac's CPU
in `installer\`. Without a Mac, push the repo to GitHub and run **Build macOS**
from the Actions tab - it builds both images on GitHub's Mac runners, and
pushing a `v*` tag attaches them to that release.

yt-dlp self-updates on a Mac exactly as on Windows. ffmpeg does not offer
updates there - the build bundles a recent static ffmpeg, which rarely matters.
Settings live in `~/Library/Application Support/CobDownloader/`.

## Run it from source

```
python cob_downloader.py
```

or double-click `Cob Downloader.bat`, which launches it with `pythonw` so no
black console window tags along.

Needs Python 3 with `pip install -r requirements.txt`, and `ffmpeg` on PATH
(`winget install --id Gyan.FFmpeg.Essentials`). The header line tells you if
either is missing.

## What the controls do

| Control | Effect |
| --- | --- |
| **I want** | Video, or audio only (the file type list swaps to audio codecs) |
| **Download** | Just the linked video, or every video in its playlist |
| **File type** | MP4 / MKV / WEBM, or MP3 / M4A / OPUS / FLAC / WAV |
| **Resolution** | A ceiling - "1080p" means 1080p or the best below it |
| **Frame rate** | Also a ceiling - "30" avoids the 60 fps variants |
| **Audio quality** | Bitrate for the converted audio file |
| **Browser cookies** | Borrow a logged-in session, see below |
| **Fetch info** | Optional - reads the first link and fills the resolution and frame rate lists with what that video actually has |

Paste as many links as you want, one per line (**Paste** appends, it does not
overwrite; **Clear** empties the box). They download one after another with the
same settings, duplicates are dropped, and a link that fails is reported at the
end without stopping the others. Ctrl+Enter starts the download.

Files land in the **Save to** folder; a playlist gets its own subfolder with
numbered files. Your choices are remembered in `settings.json` next to the
script. The Download button turns into Cancel while a download runs.

## If YouTube returns "HTTP Error 403"

YouTube now asks for a proof-of-origin token on a lot of requests. Two things
help, in this order:

1. Set **Browser cookies** to the browser you are signed into YouTube with.
   Firefox works best on Windows. Chrome and Edge lock their cookie database
   while running and encrypt it besides, so yt-dlp often cannot read them - if
   every link fails with a cookie error, close that browser completely or
   switch this back to None.
2. A JS runtime is no longer something you need to install - QuickJS ships
   inside the app (`vendor\qjs.exe`, 2 MB) and is handed to yt-dlp along with
   any deno / node / bun you happen to have on PATH. The header only complains
   if that bundled copy has gone missing.

Neither is needed for most other sites.

## Troubleshooting

`cob_downloader.py --check` (or `"Cob Downloader.exe" --check`) writes a report
saying which yt-dlp, ffmpeg and JS runtime it found, and whether it can reach
YouTube. The packaged build saves its settings and that report in
`%APPDATA%\CobDownloader\` (on a Mac, `~/Library/Application
Support/CobDownloader/`; run it as `"/Applications/Cob
Downloader.app/Contents/MacOS/Cob Downloader" --check`).

## Licence

Public domain ([The Unlicense](LICENSE)) - use it, change it, sell it, no
conditions. The bundled QuickJS-ng (`vendor\qjs.exe`) keeps its own MIT licence.
