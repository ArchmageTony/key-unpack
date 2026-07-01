"""PySide6 desktop UI for Key Unpack."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    try:
        from .app import main as run_ui
    except ImportError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            print(
                "PySide6 is required for the desktop UI. Install with: python -m pip install 'key-unpack[ui]'",
                file=sys.stderr,
            )
            return 2
        raise
    return run_ui(argv)


__all__ = ["main"]
