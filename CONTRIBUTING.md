# Contributing

Thanks for wanting to help ClipVault.

## Development

```bash
git clone https://github.com/Hharshitsingh/clipvault.git
cd clipvault
./run.sh            # start in the foreground
./run.sh --toggle   # show the flyout
```

The app is a GTK 4 / libadwaita process (`python3 -m clipvault`) plus a small GTK 3 tray helper (`python3 -m clipvault.tray_helper`). On GNOME it forces `GDK_BACKEND=x11` so clipboard events arrive without stealing focus — see `clipvault/platform.py`.

## Pull requests

1. Fork and create a branch
2. Keep changes focused
3. Test on GNOME Wayland: copy text, copy an image, Super+V, tray click, Esc, Clear all
4. Confirm typing stays smooth and no “wl-clipboard is ready” notifications appear

## Code map

| Path | Role |
|------|------|
| `clipvault/app.py` | Flyout UI and GApplication actions |
| `clipvault/clipboard_io.py` | Clipboard read/write |
| `clipvault/store.py` | Persistent history |
| `clipvault/tray_helper.py` | Top-bar menu |
| `clipvault/platform.py` | GNOME vs wlroots decisions |
