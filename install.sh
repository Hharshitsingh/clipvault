#!/usr/bin/env bash
# Install ClipVault: launcher, app icon, autostart, tray, Super+V (GNOME).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="io.github.hharshitsingh.ClipVault"
DBUS_PATH="/io/github/hharshitsingh/ClipVault"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
AUTO_DIR="${HOME}/.config/autostart"
ICON_DIR="${HOME}/.local/share/icons/hicolor"
TOGGLE="${BIN_DIR}/clipvault-toggle"
DAEMON="${BIN_DIR}/clipvault"
DESKTOP_FILE="${APP_DIR}/${APP_ID}.desktop"

mkdir -p "$BIN_DIR" "$APP_DIR" "$AUTO_DIR" "${ICON_DIR}/scalable/apps" "${ICON_DIR}/symbolic/apps"

# Stop the previous "clipboard-manager" install if it is still running
gdbus call --session --dest com.harshit.ClipboardManager \
  --object-path /com/harshit/ClipboardManager \
  --method org.freedesktop.Application.ActivateAction "quit" "[]" "{}" \
  >/dev/null 2>&1 || true
pkill -f 'python3 -m clipboard_manager' 2>/dev/null || true
pkill -f 'clipboard_manager.tray_helper' 2>/dev/null || true
rm -f "${BIN_DIR}/clipboard-manager" "${BIN_DIR}/clipboard-manager-toggle" \
      "${APP_DIR}/com.harshit.ClipboardManager.desktop" \
      "${AUTO_DIR}/com.harshit.ClipboardManager.desktop" \
      "${APP_DIR}/clipboard-manager.desktop" \
      "${AUTO_DIR}/clipboard-manager.desktop"

# ---------------------------------------------------------------- launchers
cat > "$DAEMON" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="${ROOT}\${PYTHONPATH:+:\$PYTHONPATH}"
if gdbus call --session \\
    --dest ${APP_ID} \\
    --object-path ${DBUS_PATH} \\
    --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
  echo "ClipVault already running"
  exit 0
fi
nohup python3 -m clipvault >/tmp/clipvault.log 2>&1 &
disown
echo "ClipVault started (pid \$!)"
EOF
chmod +x "$DAEMON" "${ROOT}/run.sh"

cat > "$TOGGLE" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="${ROOT}\${PYTHONPATH:+:\$PYTHONPATH}"
if gdbus call --session \\
    --dest ${APP_ID} \\
    --object-path ${DBUS_PATH} \\
    --method org.freedesktop.Application.ActivateAction \\
    "toggle" "[]" "{}" >/dev/null 2>&1; then
  exit 0
fi
nohup python3 -m clipvault --toggle >/tmp/clipvault.log 2>&1 &
disown
EOF
chmod +x "$TOGGLE"

# -------------------------------------------------------------------- icons
cp "${ROOT}/clipvault/icons/${APP_ID}.svg" "${ICON_DIR}/scalable/apps/${APP_ID}.svg"
cp "${ROOT}/clipvault/icons/${APP_ID}-symbolic.svg" "${ICON_DIR}/symbolic/apps/${APP_ID}-symbolic.svg"

python3 - "$ROOT" "$ICON_DIR" "$APP_ID" <<'PY' || echo "    (PNG icon rendering skipped)"
import sys
from pathlib import Path
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
root, icon_dir, app_id = sys.argv[1:4]
svg = Path(root) / "clipvault" / "icons" / f"{app_id}.svg"
for size in (32, 48, 64, 128, 256):
    out = Path(icon_dir) / f"{size}x{size}" / "apps"
    out.mkdir(parents=True, exist_ok=True)
    GdkPixbuf.Pixbuf.new_from_file_at_scale(str(svg), size, size, True).savev(
        str(out / f"{app_id}.png"), "png", [], []
    )
PY

# ------------------------------------------------------------ desktop entry
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=ClipVault
Comment=Windows 11–style clipboard history
Exec=${DAEMON}
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
Exec=${TOGGLE}
EOF
cp "$DESKTOP_FILE" "${AUTO_DIR}/${APP_ID}.desktop"

update-desktop-database "$APP_DIR" 2>/dev/null || true
if [[ -f "${ICON_DIR}/index.theme" ]]; then
  gtk-update-icon-cache -f -t "$ICON_DIR" 2>/dev/null || true
fi

if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions enable ubuntu-appindicators@ubuntu.com 2>/dev/null || true
fi

if command -v gsettings >/dev/null 2>&1; then
  gsettings set org.gnome.shell.keybindings toggle-message-tray "['<Super>m']" || true

  SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
  PATH_BASE="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
  FULL="${PATH_BASE}/custom-clipvault/"
  OLD="${PATH_BASE}/custom-clipboard-manager/"

  existing="$(gsettings get ${SCHEMA} custom-keybindings 2>/dev/null || echo "@as []")"
  # Drop the old binding name if present
  if [[ "$existing" == *"$OLD"* ]]; then
    existing="$(python3 - "$existing" "$OLD" <<'PY'
import ast, sys
raw, ours = sys.argv[1], sys.argv[2]
raw = raw.replace("@as ", "")
try:
    items = [i for i in ast.literal_eval(raw) if i != ours]
except Exception:
    items = []
print("@as []" if not items else repr(items))
PY
)"
    gsettings set ${SCHEMA} custom-keybindings "$existing" || true
    gsettings reset-recursively "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${OLD}" 2>/dev/null || true
    existing="$(gsettings get ${SCHEMA} custom-keybindings 2>/dev/null || echo "@as []")"
  fi

  if [[ "$existing" != *"$FULL"* ]]; then
    if [[ "$existing" == "@as []" || "$existing" == "[]" ]]; then
      gsettings set ${SCHEMA} custom-keybindings "['${FULL}']"
    else
      trimmed="${existing%]*}"
      gsettings set ${SCHEMA} custom-keybindings "${trimmed}, '${FULL}']"
    fi
  fi

  gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${FULL} name "ClipVault"
  gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${FULL} command "$TOGGLE"
  gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${FULL} binding "<Super>v"
  echo "==> Bound Super+V to ClipVault (notifications stay on Super+M)"
else
  echo "==> gsettings not found — bind Super+V manually to: $TOGGLE"
fi

if pgrep -f 'python3 -m clipvault' >/dev/null 2>&1; then
  pkill -f 'python3 -m clipvault' || true
  pkill -f 'clipvault.tray_helper' || true
  sleep 0.5
fi
"$DAEMON"

echo "==> Installed ClipVault"
echo "    Daemon:   $DAEMON"
echo "    Toggle:   $TOGGLE"
echo "    Desktop:  $DESKTOP_FILE"
echo "    Open with Super+V, the tray icon, or clipvault-toggle"
echo "    Remove with: ./uninstall.sh   (add --purge to also delete history)"
