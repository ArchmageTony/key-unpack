from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

import pyzipper

from .errors import ErrorCode, KeyUnpackError


def is_zip_archive(path: Path) -> bool:
    return path.suffix.lower() == ".zip"


def test_zip_archive(path: Path, password: str) -> ErrorCode | None:
    try:
        with pyzipper.AESZipFile(path) as archive:
            archive.pwd = password.encode("utf-8")
            for info in archive.infolist():
                if info.is_dir():
                    continue
                with archive.open(info) as handle:
                    handle.read(1)
                return None
            return None
    except RuntimeError as exc:
        if "bad password" in str(exc).lower():
            return ErrorCode.BAD_PASSWORD
        return ErrorCode.UNKNOWN_ERROR
    except (NotImplementedError, pyzipper.BadZipFile):
        return ErrorCode.UNSUPPORTED_FORMAT
    except PermissionError:
        return ErrorCode.PERMISSION_DENIED
    except OSError:
        return ErrorCode.UNKNOWN_ERROR


def extract_zip_archive(path: Path, output_dir: Path, password: str) -> ErrorCode | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with pyzipper.AESZipFile(path) as archive:
            archive.pwd = password.encode("utf-8")
            for info in archive.infolist():
                target = _safe_zip_target(output_dir, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        return None
    except RuntimeError as exc:
        if "bad password" in str(exc).lower():
            return ErrorCode.BAD_PASSWORD
        return ErrorCode.UNKNOWN_ERROR
    except (NotImplementedError, pyzipper.BadZipFile):
        return ErrorCode.UNSUPPORTED_FORMAT
    except PermissionError:
        return ErrorCode.PERMISSION_DENIED
    except OSError:
        return ErrorCode.UNKNOWN_ERROR


def _safe_zip_target(output_dir: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in {"", ".."} for part in relative.parts):
        raise KeyUnpackError(ErrorCode.PERMISSION_DENIED, f"Unsafe archive path: {name}")
    target = output_dir.joinpath(*relative.parts)
    output_resolved = output_dir.resolve(strict=False)
    target_resolved = target.resolve(strict=False)
    try:
        target_resolved.relative_to(output_resolved)
    except ValueError as exc:
        raise KeyUnpackError(
            ErrorCode.PERMISSION_DENIED,
            f"Archive entry escapes output directory: {target}",
        ) from exc
    return target
