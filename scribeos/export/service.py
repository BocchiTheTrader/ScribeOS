"""Single entry point for all export formats."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..storage.backups import zip_project
from ..storage.project_store import Project
from .common import EXTENSIONS, MIMETYPES, ExportOptions, file_stem, select_chapters


def export(project: Project, opts: ExportOptions, font_dirs: list[Path] | None = None) -> tuple[bytes, str, str]:
    """Return (data, filename, mimetype)."""
    stem = file_stem(opts.title or project.meta.name)
    if opts.format == "scribez":
        with tempfile.TemporaryDirectory() as tmp:
            out = zip_project(project.path, Path(tmp) / "p.zip")
            return out.read_bytes(), f"{stem}.scribez", MIMETYPES["scribez"]
    chapters = select_chapters(project, opts)
    if opts.scope == "current" and len(chapters) == 1:
        stem = file_stem(chapters[0].title)
    ext = EXTENSIONS[opts.format]
    if opts.format == "pdf":
        from .pdf_typst import export_pdf

        data = export_pdf(project, chapters, opts, font_dirs)
    elif opts.format == "docx":
        from .office import export_docx

        data = export_docx(project, chapters, opts)
    elif opts.format == "epub":
        from .office import export_epub

        data = export_epub(project, chapters, opts)
    elif opts.format == "md":
        from .text_formats import export_markdown

        data, ext = export_markdown(project, chapters, opts)
    elif opts.format == "html":
        from .text_formats import export_html

        data = export_html(project, chapters, opts)
    else:
        from .text_formats import export_txt

        data = export_txt(project, chapters, opts)
    return data, f"{stem}.{ext}", MIMETYPES[ext]


def pdf_preview(project: Project, opts: ExportOptions, font_dirs: list[Path] | None = None, pages: int = 4) -> list[bytes]:
    from .pdf_typst import export_pdf

    chapters = select_chapters(project, opts)
    # Preview the first chapters only, to keep it fast.
    out = export_pdf(project, chapters[:2], opts, font_dirs, fmt="png", ppi=50)
    if isinstance(out, (bytes, bytearray)):
        return [bytes(out)]
    return [bytes(p) for p in list(out)[:pages]]
