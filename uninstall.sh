#!/usr/bin/env bash
# Remove everything install.sh set up. History is kept unless --purge is given.
#
#   ./uninstall.sh            # stop app, remove launchers/icons/hotkey/autostart
#   ./uninstall.sh --purge    # ...and delete saved clipboard history + images
set -uo pipefail

APP_ID="io.github.hharshitsingh.ClipVault"
DBUS_PATH="/io/github/hharshitsingh/ClipVault"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
AUTO_DIR="${HOME}/.config/autostart"
ICON_DIR="${HOME}/.local/share/icons/hicolor"
DATA_DIR="${HOME}/.local/share/clipvault"
LEGACY_DATA="${HOME}/.local/share/clipboard-manager"
PURGE=0

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '==> %s\n' "$*"; }

step "Stopping ClipVault"
gdbus call --session \
  --dest "$APP_ID" \
  --object-path "$DBUS_PATH" \
  --method org.freedesktop.Application.ActivateAction "quit" "[]" "{}" \
  >/dev/null 2>&1 || true
gdbus call --session \
  --dest com.harshit.ClipboardManager \
  --object-path /com/harshit/ClipboardManager \
  --method org.freedesktop.Application.ActivateAction "quit" "[]" "{}" \
  >/dev/null 2>&1 || true
sleep 0.4
pkill -f 'python3 -m clipvault' 2>/dev/null || true
pkill -f 'clipvault.tray_helper' 2>/dev/null || true
pkill -f 'python3 -m clipboard_manager' 2>/dev/null || true
pkill -f 'clipboard_manager.tray_helper' 2>/dev/null || true

step "Removing launchers"
rm -f "${BIN_DIR}/clipvault" "${BIN_DIR}/clipvault-toggle" \
      "${BIN_DIR}/clipboard-manager" "${BIN_DIR}/clipboard-manager-toggle"

step "Removing desktop entries and autostart"
rm -f "${APP_DIR}/${APP_ID}.desktop" "${AUTO_DIR}/${APP_ID}.desktop" \
      "${APP_DIR}/com.harshit.ClipboardManager.desktop" \
      "${AUTO_DIR}/com.harshit.ClipboardManager.desktop" \
      "${APP_DIR}/clipboard-manager.desktop" \
      "${AUTO_DIR}/clipboard-manager.desktop"
update-desktop-database "$APP_DIR" 2>/dev/null || true

step "Removing icons"
for id in "$APP_ID" com.harshit.ClipboardManager; do
  rm -f "${ICON_DIR}/scalable/apps/${id}.svg" \
        "${ICON_DIR}/symbolic/apps/${id}-symbolic.svg"
  for size in 32 48 64 128 256; do
    rm -f "${ICON_DIR}/${size}x${size}/apps/${id}.png"
  done
done
if [[ -f "${ICON_DIR}/index.theme" ]]; then
  gtk-update-icon-cache -f -t "$ICON_DIR" 2>/dev/null || true
fi

if command -v gsettings >/dev/null 2>&1; then
  step "Restoring GNOME keybindings"
  SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
  for FULL in \
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom-clipvault/" \
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom-clipboard-manager/"; do
    gsettings reset-recursively "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${FULL}" 2>/dev/null || true
    existing="$(gsettings get ${SCHEMA} custom-keybindings 2>/dev/null || echo "@as []")"
    if [[ "$existing" == *"$FULL"* ]]; then
      new="$(python3 - "$existing" "$FULL" <<'PY'
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
      gsettings set ${SCHEMA} custom-keybindings "$new" 2>/dev/null || true
    fi
  done
  gsettings reset org.gnome.shell.keybindings toggle-message-tray 2>/dev/null || true
fi

if [[ "$PURGE" -eq 1 ]]; then
  step "Deleting clipboard history"
  rm -rf "$DATA_DIR" "$LEGACY_DATA"
else
  step "Keeping history at $DATA_DIR (use --purge to delete)"
fi
rm -f /tmp/clipvault.log /tmp/clipboard-manager.log

echo
echo "Uninstalled."
echo "  * Project folder left in place — remove with: rm -rf \"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)\""
echo "  * ~/.local/bin on PATH in ~/.bashrc was left (other tools may use it)."
echo "  * Ubuntu AppIndicators extension was left enabled."
