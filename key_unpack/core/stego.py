from __future__ import annotations

from pathlib import Path


VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov"}
ARCHIVE_SUFFIXES = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".001",
    ".z01",
)


def is_video_candidate(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


def find_embedded_archives(root: str | Path) -> list[Path]:
    archives: list[Path] = []
    for file_path in Path(root).rglob("*"):
        if not file_path.is_file():
            continue
        name = file_path.name.lower()
        if any(name.endswith(suffix) for suffix in ARCHIVE_SUFFIXES):
            archives.append(file_path)
    return sorted(archives, key=lambda item: (item.name.lower(), len(str(item))))
