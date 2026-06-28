from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Event
from typing import Any


class CancelToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def canceled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class TaskResult:
    input_file: str
    output_dir: str | None
    success: bool
    processed_file: str | None = None
    success_password: str | None = None
    password_source: str | None = None
    original_password: str | None = None
    used_encoding_variant: bool = False
    encoding_variant_name: str | None = None
    error_code: str | None = None
    message: str = ""
    stego_embedded_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskEvent:
    type: str
    stage: str | None = None
    current_file: str | None = None
    file_index: int | None = None
    file_count: int | None = None
    password_index: int | None = None
    password_count: int | None = None
    message: str = ""
    error_code: str | None = None
    result: TaskResult | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.result is not None:
            data["result"] = self.result.to_dict()
        return data
