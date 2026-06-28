from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, KeyUnpackError


@dataclass(frozen=True)
class ArchiveInput:
    requested_path: Path
    archive_path: Path
    missing_main_volume: bool = False


def archive_output_name(path: str | Path) -> str:
    file_path = Path(path)
    name = file_path.name
    lower = name.lower()
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".7z.001", ".zip.001"):
        if lower.endswith(suffix):
            return name[: -len(suffix)] or file_path.stem
    match = re.search(r"\.part\d+\.rar$", lower)
    if match:
        return name[: match.start()] or file_path.stem
    if re.search(r"\.z\d\d$", lower) or re.search(r"\.r\d\d$", lower):
        return Path(name).stem
    return file_path.stem or name


def main_volume_for(path: str | Path) -> Path:
    file_path = Path(path)
    name = file_path.name
    lower = name.lower()
    match = re.search(r"\.part\d+\.rar$", lower)
    if match:
        return file_path.with_name(name[: match.start()] + ".part1.rar")
    match = re.search(r"\.(7z|zip)\.\d{3}$", lower)
    if match:
        return file_path.with_name(name[: match.start()] + f".{match.group(1)}.001")
    if re.search(r"\.z\d\d$", lower):
        return file_path.with_suffix(".zip")
    if re.search(r"\.r\d\d$", lower):
        return file_path.with_suffix(".rar")
    return file_path


def normalize_archive_inputs(paths: list[str | Path]) -> list[ArchiveInput]:
    normalized: list[ArchiveInput] = []
    seen: set[Path] = set()
    for raw in paths:
        requested = Path(raw).expanduser()
        archive = main_volume_for(requested)
        key = archive.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            ArchiveInput(
                requested_path=requested,
                archive_path=archive,
                missing_main_volume=archive != requested and not archive.exists(),
            )
        )
    return normalized


def resolve_output_base(
    requested_path: Path,
    *,
    output_dir: str | Path | None,
    file_count: int,
) -> Path:
    if output_dir is not None:
        root = Path(output_dir).expanduser()
        if file_count == 1:
            return root
        return root / archive_output_name(requested_path)
    return requested_path.parent / archive_output_name(requested_path)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def prepare_output_dir(path: Path, overwrite: str) -> Path:
    if overwrite not in {"skip", "overwrite", "rename", "fail"}:
        raise ValueError(f"Invalid overwrite policy: {overwrite}")
    if path.exists() and overwrite == "rename" and any(path.iterdir()):
        path = unique_path(path)
    if path.exists() and overwrite == "fail" and any(path.iterdir()):
        raise KeyUnpackError(ErrorCode.FILE_CONFLICT, f"Output directory already exists: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_inside(base: Path, target: Path) -> None:
    base_resolved = base.resolve(strict=False)
    target_resolved = target.resolve(strict=False)
    if target_resolved == base_resolved:
        return
    try:
        target_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise KeyUnpackError(
            ErrorCode.PERMISSION_DENIED,
            f"Archive entry escapes output directory: {target}",
        ) from exc


def move_extracted_tree(source_dir: Path, output_base: Path, overwrite: str) -> Path:
    output_dir = prepare_output_dir(output_base, overwrite)
    moves: list[tuple[Path, Path]] = []
    for source in sorted(source_dir.rglob("*"), key=lambda item: len(item.parts)):
        if source == source_dir or source.is_dir():
            continue
        relative = source.relative_to(source_dir)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise KeyUnpackError(ErrorCode.PERMISSION_DENIED, f"Unsafe archive path: {relative}")
        target = output_dir / relative
        _ensure_inside(output_dir, target)
        if target.exists():
            if overwrite == "fail":
                raise KeyUnpackError(ErrorCode.FILE_CONFLICT, f"File already exists: {target}")
            if overwrite == "skip":
                continue
            if overwrite == "rename":
                target = unique_path(target)
            elif overwrite == "overwrite":
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        moves.append((source, target))
    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    return output_dir
