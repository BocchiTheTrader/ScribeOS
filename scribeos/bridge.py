"""Native desktop functions exposed to the app page as ``window.pywebview.api.*``.

Only the desktop window gets these; Teamwork guests in a browser never can.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404 - only used to open fixed, known folders
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from .export.common import ExportOptions
from .server.context import AppContext

log = logging.getLogger("scribeos.bridge")


class Bridge:
    def __init__(self, ctx: AppContext):
        # Attributes starting with "_" are not exposed to JavaScript by pywebview.
        self._ctx = ctx

    def _window(self):
        return self._ctx.window

    def _dialog(self, kind: int, directory: str = "", save_filename: str = "", file_types: tuple = ()) -> Optional[str]:
        w = self._window()
        if w is None:
            return None
        res = w.create_file_dialog(kind, directory=directory, save_filename=save_filename, file_types=file_types)
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    # ------------------------------------------------------------------ dialogs
    def choose_folder(self, start: str = "") -> Optional[str]:
        import webview

        return self._dialog(webview.FileDialog.FOLDER, directory=start or self._ctx.config.settings.storage.projectsDir)

    def choose_project(self) -> Optional[str]:
        """Pick a project folder (``*.scribe``)."""
        import webview

        return self._dialog(webview.FileDialog.FOLDER, directory=self._ctx.config.settings.storage.projectsDir)

    def export_to_file(self, options_json: str) -> dict:
        """Render an export and save it where the user chooses."""
        import webview

        from .export.service import export

        project = self._ctx.project
        if project is None:
            return {"ok": False, "error": "No project is open"}
        try:
            opts = ExportOptions.model_validate(json.loads(options_json))
            self._ctx.flush_rooms()
            data, filename, _ = export(project, opts, self._ctx.font_dirs())
        except Exception as e:
            return {"ok": False, "error": str(e)}
        ext = filename.rsplit(".", 1)[-1]
        target = self._dialog(webview.FileDialog.SAVE, save_filename=filename, file_types=(f"{ext.upper()} file (*.{ext})", "All files (*.*)"))
        if not target:
            return {"ok": False, "cancelled": True}
        if not target.lower().endswith("." + ext):
            target += "." + ext
        try:
            Path(target).write_bytes(data)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": target}

    # ------------------------------------------------------------------ shell
    def open_external(self, url: str) -> bool:
        if isinstance(url, str) and url.startswith(("https://", "http://", "mailto:")) and len(url) < 2048:
            webbrowser.open(url)
            return True
        return False

    def reveal(self, what: str) -> bool:
        """Open a known folder in Explorer/Finder. Arbitrary paths are not accepted."""
        ctx = self._ctx
        targets = {
            "project": ctx.project.path if ctx.project else None,
            "backups": Path(ctx.config.settings.storage.backupDir),
            "projects": Path(ctx.config.settings.storage.projectsDir),
            "fonts": ctx.paths.fonts_dir,
        }
        path = targets.get(what)
        if path is None:
            return False
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))  # nosec B606  # noqa: S606 - fixed, known directory
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # nosec B603 B607  # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # nosec B603 B607  # noqa: S603,S607
        return True

    def toggle_fullscreen(self) -> None:
        w = self._window()
        if w is not None:
            w.toggle_fullscreen()
