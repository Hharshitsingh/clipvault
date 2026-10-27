"""GTK4 flyout UI — Windows 11–style clipboard history."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_ID, APP_NAME
from .clipboard_io import ClipboardIO, can_auto_paste
from .store import ClipItem, HistoryStore

CSS_PATH = Path(__file__).with_name("style.css")
ROOT = Path(__file__).resolve().parent.parent


def _relative_time(ts: float) -> str:
    delta = int(time.time() - ts)
    if delta < 60:
        return "Just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return datetime.fromtimestamp(ts).strftime("%b %d, %H:%M")


class ClipboardWindow(Gtk.ApplicationWindow):
    def __init__(self, app: "ClipboardApp") -> None:
        super().__init__(application=app, title=APP_NAME)
        self.app_ref = app
        self.set_icon_name(APP_ID)  # dock / alt-tab icon (matches APP_ID.desktop)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_default_size(400, 520)
        self.add_css_class("clipboard-window")
        self.set_modal(False)
        self.set_hide_on_close(True)

        # Win11-like: close when focus moves to another window.
        # Only after we have actually been active once, so a slow focus grant
        # on Wayland doesn't instantly hide the flyout.
        self._was_active = False
        self.connect("notify::is-active", self._on_active_changed)

        # CAPTURE so Esc closes even when search box has focus
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

        shortcut = Gtk.ShortcutController()
        shortcut.set_scope(Gtk.ShortcutScope.LOCAL)
        shortcut.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        shortcut.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.KeyvalTrigger.new(Gdk.KEY_Escape, 0),
                Gtk.CallbackAction.new(self._on_escape_action),
            )
        )
        self.add_controller(shortcut)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.add_css_class("clipboard-frame")
        self.set_child(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header.add_css_class("clipboard-header")
        title = Gtk.Label(label="ClipVault", xalign=0)
        title.add_css_class("clipboard-title")
        subtitle_text = (
            "Click an item to paste · Esc to close"
            if can_auto_paste()
            else "Click an item to copy · then Ctrl+V to paste · Esc to close"
        )
        subtitle = Gtk.Label(label=subtitle_text, xalign=0)
        subtitle.add_css_class("clipboard-subtitle")
        header.append(title)
        header.append(subtitle)
        outer.append(header)

        self.search = Gtk.SearchEntry(placeholder_text="Search history")
        self.search.add_css_class("search-entry")
        self.search.connect("search-changed", self._on_search)
        # Enter in the search box pastes the first match
        self.search.connect("activate", lambda *_: self._activate_first_row())
        outer.append(self.search)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(360)
        self.scroller.set_max_content_height(360)
        self.scroller.set_vexpand(True)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("clip-list")
        self.list_box.set_activate_on_single_click(True)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.scroller.set_child(self.list_box)
        outer.append(self.scroller)

        self.empty = Gtk.Label(
            label="Nothing copied yet.\nCopy text or an image to start building history.",
            justify=Gtk.Justification.CENTER,
        )
        self.empty.add_css_class("empty-state")
        self.empty.set_wrap(True)
        outer.append(self.empty)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("footer-bar")

        clear_btn = Gtk.Button(label="Clear all")
        clear_btn.add_css_class("footer-btn")
        clear_btn.add_css_class("destructive")
        clear_btn.connect("clicked", self._on_clear)
        footer.append(clear_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)

        quit_btn = Gtk.Button(label="Quit ClipVault")
        quit_btn.add_css_class("footer-btn")
        quit_btn.set_tooltip_text("Stop ClipVault")
        quit_btn.connect("clicked", lambda *_: self.app_ref.activate_action("quit", None))
        footer.append(quit_btn)
        outer.append(footer)

        self.refresh()

    def _on_escape_action(self, _widget, _args) -> bool:
        self.hide_flyout()
        return True

    def _on_active_changed(self, *_args) -> None:
        if self.is_active():
            self._was_active = True
        elif self._was_active and self.is_visible():
            # Debounce: ignore brief blips (e.g. tooltip / popover)
            GLib.timeout_add(150, self._hide_if_still_inactive)

    def _hide_if_still_inactive(self) -> bool:
        if not self.is_active() and self.is_visible():
            self.hide_flyout()
        return GLib.SOURCE_REMOVE

    def _activate_first_row(self) -> None:
        row = self.list_box.get_row_at_index(0)
        if row is not None:
            self._activate_item(row.get_name())

    def _on_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_flyout()
            return True
        if keyval == Gdk.KEY_Down and self.search.has_focus():
            first = self.list_box.get_row_at_index(0)
            if first is not None:
                first.grab_focus()
                return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self.list_box.get_focus_child()
            if isinstance(row, Gtk.ListBoxRow):
                self._activate_item(row.get_name())
                return True
        return False

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self.refresh(entry.get_text())

    def _on_clear(self, *_args) -> None:
        self.app_ref.store.clear_unpinned()
        self.refresh(self.search.get_text())

    def refresh(self, query: str = "") -> None:
        while child := self.list_box.get_first_child():
            self.list_box.remove(child)

        items = self.app_ref.store.search(query)
        self.empty.set_visible(not items)
        self.scroller.set_visible(bool(items))

        for item in items:
            self.list_box.append(self._make_row(item))

    def _make_row(self, item: ClipItem) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_name(item.id)
        row.add_css_class("clip-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(2)
        box.set_margin_end(2)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left.set_hexpand(True)

        if item.kind == "image":
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    item.image_path, 120, 72, True
                )
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                img = Gtk.Image.new_from_paintable(texture)
                img.add_css_class("clip-thumb")
                img.set_size_request(120, 72)
                box.append(img)
                size = f"{pixbuf.get_width()}×{pixbuf.get_height()}"
                full = GdkPixbuf.Pixbuf.get_file_info(item.image_path)
                if full and full[1] and full[2]:
                    size = f"{full[1]}×{full[2]}"
                preview = Gtk.Label(label=f"Image · {size}", xalign=0)
            except Exception:
                preview = Gtk.Label(label="Image", xalign=0)
        else:
            preview = Gtk.Label(label=item.preview(140), xalign=0)
            preview.set_wrap(True)
            preview.set_max_width_chars(42)
            preview.set_ellipsize(Pango.EllipsizeMode.END)

        preview.add_css_class("clip-preview")
        meta = Gtk.Label(
            label=("Pinned · " if item.pinned else "") + _relative_time(item.created_at),
            xalign=0,
        )
        meta.add_css_class("clip-meta")
        left.append(preview)
        left.append(meta)
        box.append(left)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        pin = Gtk.Button()
        pin.set_icon_name("starred-symbolic" if item.pinned else "non-starred-symbolic")
        pin.set_tooltip_text("Unpin" if item.pinned else "Pin")
        pin.add_css_class("icon-btn")
        if item.pinned:
            pin.add_css_class("pinned")
        pin.connect("clicked", lambda _b, i=item.id: self._toggle_pin(i))
        actions.append(pin)

        delete = Gtk.Button()
        delete.set_icon_name("edit-delete-symbolic")
        delete.set_tooltip_text("Delete")
        delete.add_css_class("icon-btn")
        delete.connect("clicked", lambda _b, i=item.id: self._delete(i))
        actions.append(delete)

        box.append(actions)
        row.set_child(box)
        return row

    def _toggle_pin(self, item_id: str) -> None:
        self.app_ref.store.toggle_pin(item_id)
        self.refresh(self.search.get_text())

    def _delete(self, item_id: str) -> None:
        self.app_ref.store.remove(item_id)
        self.refresh(self.search.get_text())

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._activate_item(row.get_name())

    def _activate_item(self, item_id: str | None) -> None:
        if not item_id:
            return
        self.hide_flyout()
        self.app_ref.copy_item(item_id)

    def show_flyout(self) -> None:
        self._was_active = False
        self.search.set_text("")
        self.refresh()
        self.present()
        GLib.idle_add(self.search.grab_focus)

    def hide_flyout(self) -> None:
        self._was_active = False
        self.hide()


class ClipboardApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.store = HistoryStore()
        self.io: ClipboardIO | None = None
        self.window: ClipboardWindow | None = None
        self._tray_proc: subprocess.Popen | None = None
        self._toggle_on_launch = False
        self._css_provider: Gtk.CssProvider | None = None
        self._held = False

        toggle = Gio.SimpleAction.new("toggle", None)
        toggle.connect("activate", lambda *_: self.toggle_window())
        self.add_action(toggle)

        show = Gio.SimpleAction.new("show", None)
        show.connect("activate", lambda *_: self.window and self.window.show_flyout())
        self.add_action(show)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)

        # Used by the tray's "Clear all" so in-memory state stays in sync
        clear_action = Gio.SimpleAction.new("clear", None)
        clear_action.connect("activate", lambda *_: self._clear_history())
        self.add_action(clear_action)

        # Tray "click item" → copy by id through this process (no wl-copy popups)
        copy_action = Gio.SimpleAction.new("copy", GLib.VariantType.new("s"))
        copy_action.connect("activate", lambda _a, p: self.copy_item(p.get_string()))
        self.add_action(copy_action)

        self.connect("activate", self._on_activate)
        self.connect("command-line", self._on_command_line)
        self.connect("shutdown", self._on_shutdown)

    def _on_command_line(self, _app, cmdline) -> int:
        args = cmdline.get_arguments()
        # argv[0] is program name
        self._toggle_on_launch = "--toggle" in args or "-t" in args
        self.activate()
        # Ensure toggle runs even when this is a remote invocation
        if self._toggle_on_launch:
            # Defer so window exists after activate
            GLib.idle_add(self._do_toggle_once)
        return 0

    def _do_toggle_once(self) -> bool:
        if self._toggle_on_launch:
            self._toggle_on_launch = False
            self.toggle_window()
        return GLib.SOURCE_REMOVE

    def _on_activate(self, *_args) -> None:
        self._apply_system_theme()
        self._load_css()
        # Make the bundled icons resolvable even before ./install.sh ran
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.IconTheme.get_for_display(display).add_search_path(
                str(Path(__file__).with_name("icons"))
            )
        Gtk.Window.set_default_icon_name(APP_ID)

        if self.io is None:
            self.io = ClipboardIO()
            self.io.set_handlers(on_text=self._on_text, on_image=self._on_image)
            self.io.start_watching()
            GLib.timeout_add(600, self._prime_clipboard)

        if self.window is None:
            self.window = ClipboardWindow(self)
            self.window.hide()

        self._ensure_tray()
        if not self._held:
            # Keep the process alive once, even with no visible window
            self.hold()
            self._held = True

    def _apply_system_theme(self) -> None:
        """Follow the GNOME light/dark preference (DEFAULT tracks the system)."""
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

    def _ensure_tray(self) -> None:
        if self._tray_proc is not None and self._tray_proc.poll() is None:
            return
        env = os.environ.copy()
        deps = ROOT / ".deps" / "usr" / "lib" / "x86_64-linux-gnu" / "girepository-1.0"
        if deps.is_dir():
            existing = env.get("GI_TYPELIB_PATH", "")
            env["GI_TYPELIB_PATH"] = f"{deps}:{existing}" if existing else str(deps)
        env["PYTHONPATH"] = (
            f"{ROOT}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(ROOT)
        )
        try:
            self._tray_proc = subprocess.Popen(
                [sys.executable, "-m", "clipvault.tray_helper"],
                env=env,
                cwd=str(ROOT),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._tray_proc = None

    def _clear_history(self) -> None:
        self.store.clear_unpinned()
        if self.window is not None:
            self.window.refresh(self.window.search.get_text())

    def _quit(self) -> None:
        self._on_shutdown(self)
        self.quit()

    def _on_shutdown(self, *_args) -> None:
        if self.io:
            self.io.stop_watching()
        if self._tray_proc and self._tray_proc.poll() is None:
            self._tray_proc.terminate()
            self._tray_proc = None

    def _prime_clipboard(self) -> bool:
        if self.io is not None:
            self.io.read_now()
        return GLib.SOURCE_REMOVE

    def copy_item(self, item_id: str) -> bool:
        """Put a history item back on the clipboard. Returns True if found."""
        if self.io is None:
            return False
        item = self.store.get(item_id)
        if item is None:
            return False
        if item.kind == "image" and item.image_path:
            self.io.set_image_file(item.image_path)
        else:
            self.io.set_text(item.text)
            self.store.add_text(item.text)  # bump to top
        self.io.paste_keystroke()
        if self.window is not None:
            self.window.refresh(self.window.search.get_text())
        return True

    def _load_css(self) -> None:
        if self._css_provider is None:
            self._css_provider = Gtk.CssProvider()
            self._css_provider.load_from_path(str(CSS_PATH))
            display = Gdk.Display.get_default()
            if display:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    self._css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )

    def _on_text(self, text: str) -> None:
        item = self.store.add_text(text)
        if item and self.window and self.window.is_visible():
            self.window.refresh(self.window.search.get_text())

    def _on_image(self, data: bytes) -> None:
        item = self.store.add_image(data)
        if item and self.window and self.window.is_visible():
            self.window.refresh(self.window.search.get_text())

    def toggle_window(self) -> None:
        if self.window is None:
            return
        if self.window.is_visible():
            self.window.hide_flyout()
        else:
            self.window.show_flyout()


def run(argv: list[str] | None = None) -> int:
    # Under X11 (GNOME bridge) WM_CLASS derives from prgname; it must equal the
    # desktop file's StartupWMClass or the dock shows a generic icon.
    GLib.set_prgname(APP_ID)
    GLib.set_application_name(APP_NAME)
    Adw.init()
    app = ClipboardApp()
    return app.run(argv)
