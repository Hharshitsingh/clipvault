"""Decide how to talk to the clipboard on this desktop.

Why this exists
---------------
Under Wayland, a background app only hears about clipboard changes if the
compositor offers the wlroots ``data-control`` protocol. GNOME's Mutter does
not. ``wl-paste``/``wl-copy`` work around that by creating a tiny *invisible
window* for every call to grab a focus serial — which steals keyboard focus,
flashes the screen, and makes GNOME post "wl-clipboard is ready"
notifications. Polling with them makes typing unusable.

Mutter does, however, bridge the Wayland clipboard to XWayland. An X11 client
receives XFixes selection-owner-changed events with no focus and no windows.
So on GNOME we run the GTK4 app with ``GDK_BACKEND=x11`` and never spawn
wl-clipboard. On wlroots compositors (Sway, Hyprland, …) ``data-control``
exists and wl-clipboard is safe and event-driven.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache


def desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def is_gnome() -> bool:
    d = desktop()
    return "gnome" in d or "ubuntu" in d


def is_wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )


@lru_cache(maxsize=1)
def compositor_supports_data_control() -> bool:
    """True if `wl-paste --watch` can run (wlroots data-control protocol)."""
    if is_gnome():
        return False  # known: Mutter has no data-control; avoid spawning anything
    if shutil.which("wl-paste") is None:
        return False
    try:
        res = subprocess.run(
            ["wl-paste", "--watch", "true"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except subprocess.TimeoutExpired:
        return True  # still running → watch mode works
    except Exception:
        return False
    return res.returncode == 0


@lru_cache(maxsize=1)
def use_wl_clipboard() -> bool:
    """Only use wl-copy/wl-paste where they don't need the popup-window hack."""
    return (
        is_wayland_session()
        and shutil.which("wl-paste") is not None
        and shutil.which("wl-copy") is not None
        and compositor_supports_data_control()
    )


def should_use_x11_bridge() -> bool:
    """GNOME Wayland + XWayland available → run GTK4 as an X11 client."""
    return is_wayland_session() and is_gnome() and bool(os.environ.get("DISPLAY"))


def apply_gdk_backend() -> None:
    """Must run before GTK initialises (i.e. before Gtk.Application.run).

    Overrides any inherited GDK_BACKEND (desktop sessions often export
    GDK_BACKEND=wayland). Set CLIPVAULT_BACKEND=wayland|x11 to force.
    """
    forced = os.environ.get("CLIPVAULT_BACKEND", "").strip().lower()
    if forced in ("x11", "wayland"):
        os.environ["GDK_BACKEND"] = forced
        return
    if should_use_x11_bridge():
        os.environ["GDK_BACKEND"] = "x11"


@lru_cache(maxsize=1)
def can_auto_paste() -> bool:
    """True only when a Ctrl+V keystroke can actually be injected.

    wtype needs the virtual-keyboard protocol (wlroots only). xdotool works
    for X11/XWayland targets only, so we don't count it.
    """
    if shutil.which("wtype") is None:
        return False
    return is_wayland_session() and not is_gnome()
