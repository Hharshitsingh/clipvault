"""GTK3 AppIndicator tray — history list + Quit.

Runs as a separate process because AppIndicator is GTK3 while the main UI is GTK4.
The menu is exported over D-Bus (dbusmenu) and rendered by GNOME Shell, so:
  * only label + icon survive — custom child widgets are NOT transmitted,
  * the "show" signal is unreliable — we refresh via a file monitor instead.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPS_TYPELIB = ROOT / ".deps" / "usr" / "lib" / "x86_64-linux-gnu" / "girepository-1.0"
from clipvault import APP_ID, APP_NAME, DBUS_PATH, MAX_HISTORY  # noqa: E402

DATA_DIR = Path.home() / ".local" / "share" / "clipvault"
HISTORY = DATA_DIR / "history.json"

if DEPS_TYPELIB.is_dir():
    existing = os.environ.get("GI_TYPELIB_PATH", "")
    os.environ["GI_TYPELIB_PATH"] = (
        f"{DEPS_TYPELIB}:{existing}" if existing else str(DEPS_TYPELIB)
    )

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

TOGGLE = Path.home() / ".local" / "bin" / "clipvault-toggle"
PYTHONPATH = str(ROOT)
# GNOME Shell clips a top-level panel menu at screen height but gives a
# *submenu* a scrollbar, so: quick-access items at top level, full list in a submenu.
TOP_LEVEL_ITEMS = 10
MAX_MENU_ITEMS = MAX_HISTORY
THUMB = 48  # px — dbusmenu icon-data; the shell may downscale further

from clipvault.platform import can_auto_paste  # noqa: E402

AUTO_PASTE = can_auto_paste()


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{PYTHONPATH}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else PYTHONPATH
    )
    return env


def _dbus_action(name: str, param: str | None = None) -> bool:
    """Activate a GAction on the running app. `param` is an optional string arg."""
    args = "[]" if param is None else "[<" + GLib.Variant("s", param).print_(True) + ">]"
    try:
        res = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", APP_ID,
                "--object-path", DBUS_PATH,
                "--method", "org.freedesktop.Application.ActivateAction",
                name, args, "{}",
            ],
            timeout=2,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
    except Exception:
        return False


def _open_dialog(_widget=None) -> None:
    """Always open (never toggle closed) the full clipboard dialog."""
    if _dbus_action("show"):
        return
    if TOGGLE.exists():  # app not running yet → start it with the dialog open
        subprocess.Popen([str(TOGGLE)], env=_env(), start_new_session=True)


def _copy_item(item_id: str) -> None:
    """Ask the main app to put the item on the clipboard.

    The app owns the clipboard (via GDK). We deliberately do NOT shell out to
    wl-copy here: on GNOME every wl-copy call opens an invisible window, which
    steals focus and flashes the screen. If the app isn't running, start it —
    it will serve the request once up.
    """
    if _dbus_action("copy", item_id):
        return
    env = _env()
    try:
        subprocess.Popen(
            [sys.executable, "-m", "clipvault"],
            env=env,
            cwd=str(ROOT),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return
    GLib.timeout_add(1500, lambda: (_dbus_action("copy", item_id), False)[1])


def _quit_all(_widget=None) -> None:
    _dbus_action("quit")
    Gtk.main_quit()


def _clear_all(_widget=None) -> None:
    """Clear unpinned items. Prefer the running app; fall back to editing the file."""
    if _dbus_action("clear"):
        return
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return
    keep = []
    for entry in data:
        if entry.get("pinned"):
            keep.append(entry)
        elif entry.get("kind") == "image" and entry.get("image_path"):
            Path(entry["image_path"]).unlink(missing_ok=True)
    tmp = HISTORY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    tmp.replace(HISTORY)


def _preview(text: str, limit: int = 52) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text or "(empty)"


def _load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        pinned = [i for i in data if i.get("pinned")]
        rest = [i for i in data if not i.get("pinned")]
        return (pinned + rest)[:MAX_MENU_ITEMS]
    except Exception:
        return []


def _entry_menu_item(entry: dict) -> Gtk.MenuItem:
    if entry.get("kind") == "image":
        return _image_menu_item(entry)
    prefix = "★ " if entry.get("pinned") else ""
    item = Gtk.MenuItem(label=prefix + _preview(entry.get("text", "")))
    item.connect("activate", lambda _w, i=entry.get("id", ""): _copy_item(i))
    return item


def _image_menu_item(entry: dict) -> Gtk.MenuItem:
    """ImageMenuItem is what dbusmenu knows how to serialise (label + icon-data)."""
    path = entry.get("image_path", "")
    label = "★ Image" if entry.get("pinned") else "Image"
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB, THUMB, True)
        info = GdkPixbuf.Pixbuf.get_file_info(path)
        if info and info[1] and info[2]:
            label += f"  {info[1]}×{info[2]}"
        image = Gtk.Image.new_from_pixbuf(pixbuf)
    except Exception:
        image = Gtk.Image.new_from_icon_name("image-x-generic", Gtk.IconSize.MENU)

    item = Gtk.ImageMenuItem(label=label)
    item.set_image(image)
    item.set_always_show_image(True)
    item.connect("activate", lambda _w, i=entry.get("id", ""): _copy_item(i))
    return item


class Tray:
    def __init__(self) -> None:
        # Bundled icon dir as theme path so the symbolic icon works pre-install
        icon_dir = str(Path(__file__).with_name("icons"))
        self.indicator = AppIndicator.Indicator.new_with_path(
            "clipvault",
            f"{APP_ID}-symbolic",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
            icon_dir,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title(APP_NAME)

        self.menu = Gtk.Menu()
        self.dialog_proxy = Gtk.MenuItem(label="ClipVault history")
        self.dialog_proxy.connect("activate", _open_dialog)

        self._rebuild_pending = False
        self._last_mtime = 0.0
        self.rebuild()
        self.indicator.set_menu(self.menu)
        try:
            # Middle-click on the icon opens the full dialog
            self.indicator.set_secondary_activate_target(self.dialog_proxy)
        except Exception:
            pass

        # Refresh when history.json changes (atomic replace → "changes-done"/"created")
        self._monitor = Gio.File.new_for_path(str(DATA_DIR)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None
        )
        self._monitor.connect("changed", self._on_dir_changed)
        # Belt and braces: mtime poll every 3s in case monitoring is unavailable
        GLib.timeout_add_seconds(3, self._poll_mtime)

    # --------------------------------------------------------------- refresh

    def _on_dir_changed(self, _mon, file, _other, _event) -> None:
        if file.get_basename() == HISTORY.name:
            self._schedule_rebuild()

    def _poll_mtime(self) -> bool:
        try:
            mtime = HISTORY.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._last_mtime:
            self._schedule_rebuild()
        return GLib.SOURCE_CONTINUE

    def _schedule_rebuild(self) -> None:
        if self._rebuild_pending:
            return
        self._rebuild_pending = True
        GLib.timeout_add(200, self._do_rebuild)

    def _do_rebuild(self) -> bool:
        self._rebuild_pending = False
        self.rebuild()
        return GLib.SOURCE_REMOVE

    def rebuild(self) -> None:
        try:
            self._last_mtime = HISTORY.stat().st_mtime
        except OSError:
            self._last_mtime = 0.0

        for child in list(self.menu.get_children()):
            self.menu.remove(child)

        # Hidden proxy kept in the menu so secondary-activate keeps working
        self.menu.append(self.dialog_proxy)
        self.dialog_proxy.hide()

        items = _load_history()
        if not items:
            empty = Gtk.MenuItem(label="No clipboard history yet")
            empty.set_sensitive(False)
            self.menu.append(empty)
        else:
            # Quick access: most recent items directly in the menu
            for entry in items[:TOP_LEVEL_ITEMS]:
                self.menu.append(_entry_menu_item(entry))

            # Full history lives in the Super+V dialog (search, pin, scroll)
            if len(items) > TOP_LEVEL_ITEMS:
                self.menu.append(Gtk.SeparatorMenuItem())
                all_item = Gtk.MenuItem(label=f"All items ({len(items)})…")
                all_item.connect("activate", _open_dialog)
                self.menu.append(all_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        unpinned = sum(1 for i in items if not i.get("pinned"))
        clear_item = Gtk.MenuItem(
            label=f"Clear all ({unpinned})" if unpinned else "Clear all"
        )
        clear_item.set_tooltip_text("Removes everything except pinned items")
        clear_item.set_sensitive(unpinned > 0)
        clear_item.connect("activate", _clear_all)
        self.menu.append(clear_item)

        if not AUTO_PASTE:
            hint = Gtk.MenuItem(label="Click an item to copy · Ctrl+V to paste")
            hint.set_sensitive(False)
            self.menu.append(hint)

        quit_item = Gtk.MenuItem(label="Quit ClipVault")
        quit_item.connect("activate", _quit_all)
        self.menu.append(quit_item)

        self.menu.show_all()
        self.dialog_proxy.hide()


def main() -> int:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    Tray()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
