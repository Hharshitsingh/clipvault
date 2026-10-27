"""Clipboard read/write.

Two strategies, chosen by `platform.use_wl_clipboard()`:

* GDK (default on GNOME, running under XWayland): `Gdk.Clipboard::changed`
  fires via XFixes without focus. Nothing is spawned. Zero side effects.
* wl-clipboard (wlroots compositors only): event-driven `wl-paste --watch`.

Never call wl-copy/wl-paste on GNOME — each call opens an invisible window.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib  # noqa: E402

from .platform import can_auto_paste, use_wl_clipboard  # noqa: E402

__all__ = ["ClipboardIO", "can_auto_paste", "use_wl_clipboard"]

IMAGE_TYPE_PREFERENCE = ("image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif")


def _to_png(data: bytes, mime: str) -> bytes | None:
    if mime == "image/png":
        return data
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        ok, buf = pixbuf.save_to_bufferv("png", [], [])
        return bytes(buf) if ok else None
    except Exception:
        return None


class ClipboardIO:
    def __init__(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            raise RuntimeError("No Gdk display available")
        self.backend = type(display).__name__  # GdkX11Display / GdkWaylandDisplay
        self.clipboard = display.get_clipboard()
        self._suppress_until = 0.0
        self._on_text: Callable[[str], None] | None = None
        self._on_image: Callable[[bytes], None] | None = None
        self._watch = False
        self._watch_proc: subprocess.Popen | None = None
        self._last_fingerprint: str | bytes | None = None
        self._lock = threading.Lock()
        self._use_wl = use_wl_clipboard()
        self.clipboard.connect("changed", self._on_gdk_changed)

    # ------------------------------------------------------------------ setup

    def set_handlers(
        self,
        on_text: Callable[[str], None] | None = None,
        on_image: Callable[[bytes], None] | None = None,
    ) -> None:
        self._on_text = on_text
        self._on_image = on_image

    def start_watching(self) -> None:
        if self._watch:
            return
        self._watch = True
        if self._use_wl:
            threading.Thread(target=self._wl_watch_loop, daemon=True).start()
        else:
            # GDK path: record whatever is on the clipboard right now
            self.read_now()

    def stop_watching(self) -> None:
        self._watch = False
        proc = self._watch_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def read_now(self) -> None:
        """Read current clipboard through GDK (used at startup and on focus)."""
        self.clipboard.read_text_async(None, self._finish_text)

    # -------------------------------------------------------------- GDK path

    def _on_gdk_changed(self, _clipboard: Gdk.Clipboard) -> None:
        if self._use_wl:
            return  # wl-paste watcher is authoritative there
        if time.monotonic() < self._suppress_until:
            return
        self.read_now()

    def _finish_text(self, clipboard: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            text = None
        if text:
            if text.strip() and text != self._last_fingerprint and self._on_text:
                self._last_fingerprint = text
                self._on_text(text)
            return
        formats = clipboard.get_formats()
        if any(formats.contain_mime_type(m) for m in IMAGE_TYPE_PREFERENCE):
            clipboard.read_texture_async(None, self._finish_texture)

    def _finish_texture(self, clipboard: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error:
            return
        if texture is None or not self._on_image:
            return
        try:
            data = bytes(texture.save_to_png_bytes().get_data())
            if data and data != self._last_fingerprint:
                self._last_fingerprint = data
                self._on_image(data)
        except Exception:
            pass

    # ------------------------------------------------- wl-clipboard (wlroots)

    def _wl_watch_loop(self) -> None:
        self._wl_read_once()
        while self._watch:
            proc: subprocess.Popen | None = None
            started = time.monotonic()
            try:
                proc = subprocess.Popen(
                    ["wl-paste", "--watch", "echo", "x"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                self._watch_proc = proc
                assert proc.stdout is not None
                for _line in proc.stdout:
                    if not self._watch:
                        break
                    self._wl_read_once()
            except Exception:
                pass
            finally:
                if proc is not None:
                    try:
                        proc.wait(timeout=1)
                    except Exception:
                        proc.kill()
                self._watch_proc = None
            if not self._watch:
                break
            if time.monotonic() - started < 2.0:
                # Died instantly → protocol unsupported after all; use GDK instead
                self._use_wl = False
                GLib.idle_add(self.read_now)
                return
            time.sleep(1.0)

    def _wl_read_once(self) -> None:
        if time.monotonic() < self._suppress_until:
            return
        with self._lock:
            try:
                listed = subprocess.run(
                    ["wl-paste", "--list-types"], capture_output=True, text=True, timeout=2
                )
                types = [t.strip() for t in (listed.stdout or "").splitlines() if t.strip()]
                if not types:
                    return
                image_mime = next((m for m in IMAGE_TYPE_PREFERENCE if m in types), None)
                has_text = any(
                    t.startswith("text/plain") or t in ("UTF8_STRING", "STRING", "TEXT")
                    for t in types
                )
                if image_mime and not has_text:
                    raw = subprocess.run(
                        ["wl-paste", "--type", image_mime], capture_output=True, timeout=8
                    )
                    if raw.returncode == 0 and raw.stdout:
                        png = _to_png(raw.stdout, image_mime)
                        if png and png != self._last_fingerprint:
                            self._last_fingerprint = png
                            if self._on_image:
                                GLib.idle_add(self._on_image, png)
                    return
                if has_text:
                    raw = subprocess.run(
                        ["wl-paste", "--no-newline"], capture_output=True, timeout=5
                    )
                    if raw.returncode == 0:
                        text = raw.stdout.decode("utf-8", errors="replace")
                        if text.strip() and text != self._last_fingerprint:
                            self._last_fingerprint = text
                            if self._on_text:
                                GLib.idle_add(self._on_text, text)
            except Exception:
                pass

    # ---------------------------------------------------------------- writing

    def _suppress(self, seconds: float = 0.8) -> None:
        self._suppress_until = time.monotonic() + seconds

    def set_text(self, text: str) -> None:
        self._suppress()
        self._last_fingerprint = text
        try:
            self.clipboard.set(text)
        except Exception:
            pass
        if self._use_wl:
            try:
                subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=False, timeout=3)
            except Exception:
                pass

    def set_image_file(self, path: str | Path) -> None:
        path = Path(path)
        if not path.exists():
            return
        self._suppress()
        data = path.read_bytes()
        self._last_fingerprint = data
        try:
            self.clipboard.set(Gdk.Texture.new_from_filename(str(path)))
        except Exception:
            pass
        if self._use_wl:
            try:
                subprocess.run(
                    ["wl-copy", "--type", "image/png"], input=data, check=False, timeout=5
                )
            except Exception:
                pass

    def paste_keystroke(self) -> bool:
        if not can_auto_paste():
            return False

        def run() -> None:
            try:
                subprocess.run(
                    ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"],
                    capture_output=True,
                    timeout=3,
                )
            except Exception:
                pass

        threading.Timer(0.2, run).start()
        return True
