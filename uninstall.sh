#!/bin/sh
# Removes what install.sh added. The library (~/.local/share/local.koen.Music) is kept.
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
ID=local.koen.Music
rm -f "$SHARE/applications/$ID.desktop" "$SHARE/dbus-1/services/$ID.service" "$SHARE/gnome-shell/search-providers/$ID.search-provider.ini" \
      "$SHARE/icons/hicolor/scalable/apps/$ID.svg" "$SHARE/icons/hicolor/symbolic/apps/$ID-symbolic.svg"
command -v gnome-extensions >/dev/null 2>&1 && gnome-extensions disable music-top-bar@local.koen 2>/dev/null
rm -rf "$SHARE/gnome-shell/extensions/music-top-bar@local.koen"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$SHARE/applications" || true
echo "Music is removed from the app grid. Your library is still in $SHARE/$ID."
