"""Application-wide settings, stored in the per-user config directory."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Literal

from platformdirs import user_cache_dir, user_config_dir, user_data_dir, user_documents_dir
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import APP_NAME
from .storage.fileio import atomic_write_json, read_json

ThemeName = Literal["dark", "light", "book", "manga", "custom", "system"]
Role = Literal["editor", "commenter", "viewer"]


class CustomTheme(BaseModel):
    base: Literal["dark", "light"] = "dark"
    bg: str = "#101014"
    surface: str = "#17171c"
    text: str = "#ececf1"
    muted: str = "#9b9ba7"
    accent: str = "#22c55e"
    border: str = "#2a2a33"

    @field_validator("bg", "surface", "text", "muted", "accent", "border")
    @classmethod
    def _hex(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("colour must be #rrggbb")
        return v


class EditorSettings(BaseModel):
    theme: ThemeName = "dark"
    customTheme: CustomTheme = Field(default_factory=CustomTheme)
    bodyFont: str = "Lora"
    headingFont: str = "Inter"
    uiFont: str = "Inter"
    fontSize: int = Field(18, ge=12, le=36)
    lineHeight: float = Field(1.6, ge=1.0, le=3.0)
    letterSpacing: float = Field(0.0, ge=-0.05, le=0.3)
    paragraphSpacing: float = Field(1.0, ge=0.0, le=3.0)
    paragraphIndent: bool = False
    contentWidth: int = Field(800, ge=480, le=1600)
    uiScale: int = Field(100, ge=80, le=160)
    typewriter: bool = False
    focusMode: bool = False
    verticalText: bool = False
    smartTypography: bool = True
    spellcheck: bool = True
    dailyGoal: int = Field(1000, ge=0, le=100000)
    sprintMinutes: int = Field(25, ge=1, le=240)
    reduceMotion: bool = False
    codexHighlight: bool = True

    @field_validator("bodyFont", "headingFont", "uiFont")
    @classmethod
    def _font(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[A-Za-z0-9 \-']{1,80}", v):
            raise ValueError("invalid font name")
        return v


class StorageSettings(BaseModel):
    projectsDir: str = ""
    backupDir: str = ""
    backupIntervalMin: int = Field(10, ge=1, le=1440)
    backupKeep: int = Field(20, ge=1, le=500)
    maxUploadMB: int = Field(200, ge=1, le=10_000)
    snapshotIntervalMin: int = Field(30, ge=1, le=1440)


class VoiceSettings(BaseModel):
    model: Literal["tiny", "base", "small", "medium"] = "base"
    language: str = Field("auto", pattern=r"^(auto|[a-z]{2,3})$")
    commands: bool = True


class TeamSettings(BaseModel):
    port: int = Field(8765, ge=1024, le=65535)
    defaultRole: Role = "editor"
    guestMaxUploadMB: int = Field(25, ge=1, le=2000)
    https: bool = False


class AppSettings(BaseModel):
    schemaVersion: int = 2
    editor: EditorSettings = Field(default_factory=EditorSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    team: TeamSettings = Field(default_factory=TeamSettings)
    recentProjects: list[str] = Field(default_factory=list)
    authorName: str = Field("", max_length=120)
    window: dict = Field(default_factory=dict)


class Paths:
    def __init__(self, base: Path | None = None):
        if base is not None:
            self.config = base / "config"
            self.data = base / "data"
            self.cache = base / "cache"
            self.documents = base / "Documents"
        else:
            self.config = Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))
            self.data = Path(user_data_dir(APP_NAME, appauthor=False))
            self.cache = Path(user_cache_dir(APP_NAME, appauthor=False))
            self.documents = Path(user_documents_dir())
        for p in (self.config, self.data, self.cache):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def settings_file(self) -> Path:
        return self.config / "settings.json"

    @property
    def fonts_dir(self) -> Path:
        d = self.data / "fonts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def models_dir(self) -> Path:
        d = self.cache / "whisper"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def stats_file(self) -> Path:
        return self.data / "stats.json"

    @property
    def webview_storage(self) -> Path:
        d = self.data / "webview"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def tls_dir(self) -> Path:
        d = self.data / "tls"
        d.mkdir(parents=True, exist_ok=True)
        return d


class Config:
    """Thread-safe wrapper around :class:`AppSettings` persisted as JSON."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self._lock = threading.RLock()
        raw = read_json(paths.settings_file, {}) or {}
        try:
            self.settings = AppSettings.model_validate(raw)
        except ValidationError:
            # Keep a copy of the unreadable file instead of silently discarding it.
            if paths.settings_file.exists():
                os.replace(paths.settings_file, paths.settings_file.with_suffix(".invalid.json"))
            self.settings = AppSettings()
        if not self.settings.storage.projectsDir:
            self.settings.storage.projectsDir = str(paths.documents / APP_NAME)
        if not self.settings.storage.backupDir:
            self.settings.storage.backupDir = str(paths.data / "backups")
        self.save()

    def save(self) -> None:
        with self._lock:
            atomic_write_json(self.paths.settings_file, self.settings.model_dump())

    def update(self, patch: dict) -> AppSettings:
        with self._lock:
            merged = _deep_merge(self.settings.model_dump(), patch)
            self.settings = AppSettings.model_validate(merged)
            self.save()
            return self.settings

    def add_recent(self, path: str) -> None:
        with self._lock:
            norm = os.path.normcase(os.path.abspath(path))
            rec = [p for p in self.settings.recentProjects if os.path.normcase(os.path.abspath(p)) != norm]
            self.settings.recentProjects = [os.path.abspath(path)] + rec[:9]
            self.save()

    def remove_recent(self, path: str) -> None:
        with self._lock:
            norm = os.path.normcase(os.path.abspath(path))
            self.settings.recentProjects = [p for p in self.settings.recentProjects if os.path.normcase(os.path.abspath(p)) != norm]
            self.save()


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
