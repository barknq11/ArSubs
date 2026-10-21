<img src="assets/icon.png" width="96" align="right" alt="ArSubs icon">

# ArSubs

A small Windows app that downloads the right **Arabic subtitle** for a movie, TV episode or anime episode and saves it next to the video.

## Download

Get `ArSubs.exe` from the [Releases](../../releases) page. You don't need to install anything.

## Usage

1. Open `ArSubs.exe` and click **Settings**. Add a free API key from at least one of these sites:
   - **SubDL** (best for Arabic and anime): sign up at [subdl.com](https://subdl.com), then copy your key from the API page in your account panel.
   - **OpenSubtitles**: [opensubtitles.com](https://www.opensubtitles.com) → Profile → API consumers. Adding your username and password is optional and raises the daily download limit.
2. Click **Choose video file(s)…**, or **Choose folder…** to do a whole season.
3. The subtitle is saved as `VideoName.ar.srt` next to the video. VLC, MPV and MPC load it automatically.

If the subtitle it picked is out of sync, double-click another match in the list to replace it.

## Features

- Detects the title, season, episode and year from file names, including `S01E05`, `1x05`, anime names like `[Group] Title - 05`, and `Episode 1100`.
- Matches by exact file hash on OpenSubtitles when possible, and ranks the rest by how close the release name is.
- Pulls the right episode out of season-pack zips.
- Converts old Windows-1256 Arabic subtitles to UTF-8, so the text displays correctly.
- Choosing a folder skips videos that already have an Arabic subtitle.

## Run from source

Requires Python 3.9+ and uses only the standard library:

```
python arsubs.pyw
```

Build the exe:

```
pip install pyinstaller
pyinstaller --onefile --windowed --name ArSubs --icon assets/icon.ico --add-data "assets/icon.ico;assets" arsubs.pyw
```

## License

MIT
