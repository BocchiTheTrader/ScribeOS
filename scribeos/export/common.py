"""Shared export options and helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..storage.project_store import Project

Format = Literal["pdf", "docx", "epub", "md", "html", "txt", "scribez"]
Preset = Literal["manuscript", "paperback", "a4", "a5", "manga"]


class ExportOptions(BaseModel):
    format: Format = "pdf"
    scope: Literal["project", "chapters", "current"] = "project"
    chapterIds: list[str] = Field(default_factory=list, max_length=5000)
    currentChapterId: Optional[str] = None
    preset: Preset = "paperback"
    title: Optional[str] = Field(None, max_length=300)
    author: Optional[str] = Field(None, max_length=200)
    titlePage: bool = True
    toc: bool = True
    chapterNumbers: bool = True
    pageNumbers: bool = True
    includeImages: bool = True
    sceneBreak: str = Field("* * *", max_length=20)
    font: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9 \-']{1,80}$")
    fontSize: Optional[float] = Field(None, ge=6, le=30)


@dataclass
class Chapter:
    id: str
    title: str
    doc: dict


def select_chapters(project: Project, opts: ExportOptions) -> list[Chapter]:
    nodes = project.meta.chapters()
    if opts.scope == "current":
        cid = opts.currentChapterId or project.meta.activeChapterId
        nodes = [n for n in nodes if n.id == cid]
    elif opts.scope == "chapters":
        wanted = set(opts.chapterIds)
        nodes = [n for n in nodes if n.id in wanted]
    if not nodes:
        raise ValueError("Nothing to export: no chapters selected")
    return [Chapter(n.id, n.title, project.get_chapter(n.id)["doc"]) for n in nodes]


def file_stem(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    return (s or "export")[:80]


def asset_path(project: Project, src: str) -> Optional[Path]:
    name = src.rsplit("/", 1)[-1]
    try:
        p = project.assets.path(name)
    except ValueError:
        return None
    return p if p.exists() else None


EXTENSIONS = {"pdf": "pdf", "docx": "docx", "epub": "epub", "md": "md", "html": "html", "txt": "txt", "scribez": "scribez"}
MIMETYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "epub": "application/epub+zip",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "scribez": "application/zip",
    "zip": "application/zip",
}
