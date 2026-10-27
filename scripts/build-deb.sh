#!/usr/bin/env bash
# Build clipvault_<version>_all.deb in dist/
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c "from pathlib import Path; import re
t=Path('${ROOT}/clipvault/__init__.py').read_text()
print(re.search(r'__version__ = \"([^\"]+)\"', t).group(1))")"
APP_ID="io.github.hharshitsingh.ClipVault"
NAME="clipvault"
ARCH="all"
PKG="${NAME}_${VERSION}_${ARCH}"
STAGE="${ROOT}/dist/${PKG}"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/${NAME}" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" \
         "$STAGE/usr/share/icons/hicolor/symbolic/apps" \
         "$STAGE/usr/share/doc/${NAME}"

cp -a "${ROOT}/clipvault" "$STAGE/usr/share/${NAME}/"
find "$STAGE/usr/share/${NAME}" -type d -name '__pycache__' -exec rm -rf {} +

cat > "$STAGE/usr/bin/clipvault" <<'EOF'
#!/usr/bin/env bash
export PYTHONPATH="/usr/share/clipvault${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m clipvault "$@"
EOF
cat > "$STAGE/usr/bin/clipvault-toggle" <<'EOF'
#!/usr/bin/env bash
export PYTHONPATH="/usr/share/clipvault${PYTHONPATH:+:$PYTHONPATH}"
if gdbus call --session \
    --dest io.github.hharshitsingh.ClipVault \
    --object-path /io/github/hharshitsingh/ClipVault \
    --method org.freedesktop.Application.ActivateAction \
    "toggle" "[]" "{}" >/dev/null 2>&1; then
  exit 0
fi
exec python3 -m clipvault --toggle
EOF
chmod 755 "$STAGE/usr/bin/clipvault" "$STAGE/usr/bin/clipvault-toggle"

cp "${ROOT}/clipvault/icons/${APP_ID}.svg" \
   "$STAGE/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
cp "${ROOT}/clipvault/icons/${APP_ID}-symbolic.svg" \
   "$STAGE/usr/share/icons/hicolor/symbolic/apps/${APP_ID}-symbolic.svg"

cat > "$STAGE/usr/share/applications/${APP_ID}.desktop" <<EOF
[Desktop Entry]
Name=ClipVault
Comment=Windows 11–style clipboard history
Exec=/usr/bin/clipvault
Icon=${APP_ID}
Terminal=false
Type=Application
Categories=Utility;
Keywords=clipboard;history;paste;copy;clipvault;
StartupNotify=false
StartupWMClass=${APP_ID}
X-GNOME-Autostart-enabled=true
Actions=toggle;

[Desktop Action toggle]
Name=Show clipboard history
Exec=/usr/bin/clipvault-toggle
EOF

cp "${ROOT}/LICENSE" "$STAGE/usr/share/doc/${NAME}/copyright"
cp "${ROOT}/CHANGELOG.md" "$STAGE/usr/share/doc/${NAME}/"

INSTALLED_SIZE="$(du -sk "$STAGE/usr" | awk '{print $1}')"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-gtk-3.0, gir1.2-gdkpixbuf-2.0
Recommends: gir1.2-ayatanaappindicator3-0.1
Suggests: wl-clipboard, wtype
Installed-Size: ${INSTALLED_SIZE}
Maintainer: Harshit Singh <hharshitsingh@users.noreply.github.com>
Homepage: https://github.com/Hharshitsingh/clipvault
Description: Windows 11–style clipboard history for Linux
 ClipVault keeps a searchable history of copied text and images, opens with
 Super+V, and lives in the GNOME top bar. On GNOME it watches the clipboard
 through XWayland so it never steals keyboard focus.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache -q -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"
cp "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

dpkg-deb --root-owner-group --build "$STAGE" "${ROOT}/dist/${PKG}.deb"
echo "Built ${ROOT}/dist/${PKG}.deb"
