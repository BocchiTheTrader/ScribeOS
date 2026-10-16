"""Pydantic models for everything stored inside a ``.scribe`` project folder."""

from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 2
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

Id = Annotated[str, Field(pattern=ID_RE.pattern)]


def new_id(prefix: str = "") -> str:
    # Time-ordered and unguessable: 10 hex chars of ms timestamp + 10 random.
    return f"{prefix}{int(time.time() * 1000):011x}{secrets.token_hex(5)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ChapterNode(BaseModel):
    type: Literal["chapter"] = "chapter"
    id: Id
    title: str = Field("Untitled", max_length=300)
    status: Literal["todo", "draft", "revised", "final"] = "draft"
    synopsis: str = Field("", max_length=5000)
    label: Optional[str] = None
    wordCount: int = Field(0, ge=0)
    target: int = Field(0, ge=0, le=10_000_000)

    @field_validator("label")
    @classmethod
    def _label(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not HEX_RE.match(v):
            raise ValueError("label must be #rrggbb")
        return v


class PartNode(BaseModel):
    type: Literal["part"] = "part"
    id: Id
    title: str = Field("Part", max_length=300)
    collapsed: bool = False
    children: list[ChapterNode] = Field(default_factory=list)


TreeNode = Annotated[Union[ChapterNode, PartNode], Field(discriminator="type")]


class Targets(BaseModel):
    projectWords: int = Field(0, ge=0, le=100_000_000)
    deadline: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ProjectMeta(BaseModel):
    schemaVersion: int = SCHEMA_VERSION
    id: Id
    name: str = Field(..., min_length=1, max_length=200)
    author: str = Field("", max_length=200)
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)
    theme: Optional[Literal["dark", "light", "book", "manga", "custom"]] = None
    mode: Literal["prose", "manga", "screenplay"] = "prose"
    tree: list[TreeNode] = Field(default_factory=list)
    activeChapterId: Optional[str] = None
    targets: Targets = Field(default_factory=Targets)

    def chapters(self) -> list[ChapterNode]:
        out: list[ChapterNode] = []
        for n in self.tree:
            if isinstance(n, PartNode):
                out.extend(n.children)
            else:
                out.append(n)
        return out

    def find_chapter(self, cid: str) -> Optional[ChapterNode]:
        return next((c for c in self.chapters() if c.id == cid), None)


NOTE_COLORS = ("yellow", "cyan", "purple", "green", "pink")


class Note(BaseModel):
    id: Id
    text: str = Field("", max_length=20000)
    color: Literal["yellow", "cyan", "purple", "green", "pink"] = "yellow"
    x: int = Field(200, ge=-2000, le=20000)
    y: int = Field(200, ge=-2000, le=20000)
    w: int = Field(250, ge=120, le=2000)
    h: int = Field(200, ge=80, le=2000)


class CodexEntry(BaseModel):
    id: Id
    name: str = Field(..., min_length=1, max_length=120)
    kind: Literal["character", "place", "item", "faction", "lore", "other"] = "character"
    aliases: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=20)
    description: str = Field("", max_length=20000)
    color: str = "#f59e0b"
    image: Optional[str] = Field(None, pattern=r"^/api/assets/[0-9a-f]{64}\.(png|jpg|gif|webp|avif)$")

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not HEX_RE.match(v):
            raise ValueError("color must be #rrggbb")
        return v
