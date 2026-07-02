from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .config import DataPaths
from .events import TaskResult
from .passwords import now_iso


def append_success_log(paths: DataPaths, result: TaskResult, config: dict[str, Any]) -> None:
    if result.success_password is None or not config.get("log_success_password", True):
        return
    payload: dict[str, Any] = {
        "extracted_at": now_iso(),
        "archive_name": Path(result.input_file).name,
        "password": result.success_password,
    }
    if result.used_encoding_variant:
        payload["original_password"] = result.original_password
        payload["used_encoding_variant"] = True
        payload["encoding_variant_name"] = result.encoding_variant_name

    paths.extract_log_path.parent.mkdir(parents=True, exist_ok=True)
    with paths.extract_log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    trim_log(
        paths.extract_log_path,
        max_records=int(config.get("max_log_records") or 0),
        max_bytes=int(config.get("max_log_bytes") or 0),
    )


def trim_log(path: Path, *, max_records: int, max_bytes: int) -> None:
    if not path.exists():
        return
    if max_records <= 0 and max_bytes <= 0:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if max_records > 0 and len(lines) > max_records:
        lines = lines[-max_records:]
    if max_bytes > 0:
        while lines and sum(len(line.encode("utf-8")) + 1 for line in lines) > max_bytes:
            lines.pop(0)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line)
                handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
