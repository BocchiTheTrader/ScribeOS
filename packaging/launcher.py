"""Entry point for frozen builds (PyInstaller cannot run a package's __main__ with relative imports)."""

import multiprocessing
import sys


def _ensure_streams():
    """A windowed (console=False) build starts with sys.stdout/sys.stderr set to None.

    Libraries such as uvicorn call ``sys.stderr.isatty()`` while configuring logging and crash,
    so route both streams to a log file in the user's log folder instead.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from pathlib import Path

        from platformdirs import user_log_dir

        log_dir = Path(user_log_dir("ScribeOS", appauthor=False))
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "scribeos.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        import os

        stream = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _ensure_streams()
    from scribeos.app import main

    sys.exit(main())
