"""python -m clipvault [--toggle]"""

from __future__ import annotations

import sys

# Pick the GDK backend *before* GTK is initialised (see platform.py for why).
from .platform import apply_gdk_backend

apply_gdk_backend()

from .app import run  # noqa: E402


def main() -> None:
    raise SystemExit(run(sys.argv))


if __name__ == "__main__":
    main()
