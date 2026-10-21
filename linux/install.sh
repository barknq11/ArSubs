#!/bin/sh
# Installs ArSubs for the current user and adds it to the app menu.
set -e
cd "$(dirname "$0")"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
install -m 755 ArSubs "$HOME/.local/bin/arsubs"
cp icon.png "$HOME/.local/share/icons/hicolor/256x256/apps/arsubs.png"
cat > "$HOME/.local/share/applications/arsubs.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ArSubs
Comment=Download Arabic subtitles for movies, series and anime
Exec=$HOME/.local/bin/arsubs %F
Icon=arsubs
Terminal=false
Categories=AudioVideo;Video;Utility;
MimeType=video/x-matroska;video/mp4;video/x-msvideo;video/quicktime;video/webm;
DESKTOP
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "ArSubs installed. Open it from your app menu, or run: arsubs"
