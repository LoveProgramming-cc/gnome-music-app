#!/bin/sh
# Adds Music to the GNOME app grid (for this user only): launcher, icon, and the
# search provider that lets the Activities overview find songs and playlists.
# The app keeps running from this folder — move the folder, run this again.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
ID=local.koen.Music

mkdir -p "$SHARE/applications" "$SHARE/icons/hicolor/scalable/apps" "$SHARE/icons/hicolor/symbolic/apps" \
         "$SHARE/gnome-shell/search-providers" "$SHARE/dbus-1/services"
sed "s|@MUSIC@|$HERE/music|" "$HERE/data/$ID.desktop.in" > "$SHARE/applications/$ID.desktop"
sed "s|@MUSIC@|$HERE/music|" "$HERE/data/$ID.service.in" > "$SHARE/dbus-1/services/$ID.service"
cp "$HERE/data/$ID.search-provider.ini" "$SHARE/gnome-shell/search-providers/"
cp "$HERE/data/icons/hicolor/scalable/apps/$ID.svg" "$SHARE/icons/hicolor/scalable/apps/"
cp "$HERE/data/icons/hicolor/symbolic/apps/$ID-symbolic.svg" "$SHARE/icons/hicolor/symbolic/apps/"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$SHARE/applications" || true
command -v gtk4-update-icon-cache >/dev/null 2>&1 && gtk4-update-icon-cache -q -t -f "$SHARE/icons/hicolor" 2>/dev/null || true

# The top-bar panel (cover, controls, timeline) is a GNOME Shell extension.
EXT="$SHARE/gnome-shell/extensions/music-top-bar@local.koen"
mkdir -p "$EXT"
cp "$HERE/shell-extension/"* "$EXT/"
if command -v gnome-extensions >/dev/null 2>&1 && gnome-extensions enable music-top-bar@local.koen 2>/dev/null; then
    echo "Music is installed for $USER, with its top-bar panel."
else
    echo "Music is installed for $USER. Search results appear after the next login."
    echo "For the top-bar panel: log out and in once (GNOME only finds new extensions at login), then run"
    echo "    gnome-extensions enable music-top-bar@local.koen"
fi
