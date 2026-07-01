from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Iterable

from .config import AppConfig
from .errors import ErrorCode, KeyUnpackError
from .events import CancelToken, TaskEvent, TaskResult
from .logs import append_success_log
from .passwords import PasswordCandidate, PasswordStore, build_candidates
from .paths import (
    ArchiveInput,
    move_extracted_tree,
    normalize_archive_inputs,
    resolve_output_base,
)
from .sevenzip import SevenZipRunner, classify_result
from .stego import find_embedded_archives, is_video_candidate

OVERWRITE_POLICIES = ("skip", "overwrite", "rename", "fail")


@dataclass
class ExtractRequest:
    archives: Iterable[str | Path]
    output_dir: str | Path | None = None
    overwrite: str = "rename"
    data_dir: str | Path | None = None
    enable_stego: bool = True
    enable_password_encoding_compat: bool | None = None
    sevenzip_path: str | Path | None = None
    temp_dir: str | Path | None = None
    cancel_token: CancelToken | None = None


def extract_many(request: ExtractRequest) -> Generator[TaskEvent, None, None]:
    archives = list(request.archives)
    if request.overwrite not in OVERWRITE_POLICIES:
        raise ValueError(f"Invalid overwrite policy: {request.overwrite}")
    try:
        app_config = AppConfig.load(request.data_dir)
    except KeyUnpackError as exc:
        yield _failure_event(
            None,
            None,
            None,
            TaskResult(
                input_file="",
                output_dir=None,
                success=False,
                error_code=exc.code.value,
                message=exc.message,
            ),
        )
        return

    if request.sevenzip_path is not None:
        app_config.local["sevenzip_path"] = str(request.sevenzip_path)
    if request.temp_dir is not None:
        app_config.local["temp_dir"] = str(request.temp_dir)

    store = PasswordStore(app_config.paths)
    inputs = normalize_archive_inputs(archives)
    file_count = len(inputs)
    for index, archive_input in enumerate(inputs, start=1):
        token = request.cancel_token
        if token is not None and token.canceled:
            yield _failure_event(
                archive_input.requested_path,
                index,
                file_count,
                TaskResult(
                    input_file=str(archive_input.requested_path),
                    output_dir=None,
                    success=False,
                    error_code=ErrorCode.CANCELED.value,
                    message="Canceled",
                ),
            )
            continue
        yield TaskEvent(
            type="started",
            stage="detecting",
            current_file=str(archive_input.requested_path),
            file_index=index,
            file_count=file_count,
            message="Detecting archive",
        )
        try:
            result = yield from _extract_one(
                archive_input,
                index=index,
                file_count=file_count,
                request=request,
                app_config=app_config,
                store=store,
            )
        except KeyUnpackError as exc:
            result = TaskResult(
                input_file=str(archive_input.requested_path),
                processed_file=str(archive_input.archive_path),
                output_dir=None,
                success=False,
                error_code=exc.code.value,
                message=exc.message,
            )
        if result.success:
            yield TaskEvent(
                type="success",
                stage="completed",
                current_file=result.input_file,
                file_index=index,
                file_count=file_count,
                message=result.message,
                result=result,
            )
        else:
            yield _failure_event(archive_input.requested_path, index, file_count, result)


def _extract_one(
    archive_input: ArchiveInput,
    *,
    index: int,
    file_count: int,
    request: ExtractRequest,
    app_config: AppConfig,
    store: PasswordStore,
) -> Generator[TaskEvent, None, TaskResult]:
    if archive_input.missing_main_volume:
        return TaskResult(
            input_file=str(archive_input.requested_path),
            processed_file=str(archive_input.archive_path),
            output_dir=None,
            success=False,
            error_code=ErrorCode.VOLUME_MISSING.value,
            message=f"Main volume not found: {archive_input.archive_path}",
        )
    if not archive_input.archive_path.exists():
        return TaskResult(
            input_file=str(archive_input.requested_path),
            processed_file=str(archive_input.archive_path),
            output_dir=None,
            success=False,
            error_code=ErrorCode.UNSUPPORTED_FORMAT.value,
            message=f"Input file not found: {archive_input.archive_path}",
        )

    timeout = int(app_config.config.get("command_timeout_seconds") or 0) or None
    runner = SevenZipRunner(
        request.sevenzip_path or app_config.sevenzip_path,
        timeout_seconds=timeout,
    )
    temp_parent = request.temp_dir or app_config.temp_dir
    task_temp = Path(tempfile.mkdtemp(prefix="key-unpack-", dir=temp_parent))
    configured_output_dir = None
    if app_config.config.get("output_strategy") == "fixed":
        configured_output_dir = app_config.local.get("output_dir")
    output_base = resolve_output_base(
        archive_input.requested_path,
        output_dir=request.output_dir or configured_output_dir,
        file_count=file_count,
    )
    enable_compat = (
        bool(app_config.config.get("enable_password_encoding_compat", True))
        if request.enable_password_encoding_compat is None
        else request.enable_password_encoding_compat
    )
    try:
        result = yield from _try_archive(
            archive_path=archive_input.archive_path,
            input_file=archive_input.requested_path,
            output_base=output_base,
            task_temp=task_temp,
            store=store,
            app_config=app_config,
            runner=runner,
            request=request,
            enable_compat=enable_compat,
            index=index,
            file_count=file_count,
            testing_stage="testing_password",
            extract_stage="extracting",
            stego_embedded_file=None,
        )
        if result.success:
            return result
        if (
            result.error_code == ErrorCode.UNSUPPORTED_FORMAT.value
            and request.enable_stego
            and is_video_candidate(archive_input.requested_path)
        ):
            stego_dir = task_temp / "stego"
            yield TaskEvent(
                type="stage",
                stage="extracting_stego",
                current_file=str(archive_input.requested_path),
                file_index=index,
                file_count=file_count,
                message="Extracting embedded archive from video",
            )
            stego_result = runner.extract_stego(
                archive_input.archive_path,
                stego_dir,
                cancel_token=request.cancel_token,
            )
            stego_error = classify_result(stego_result)
            if stego_error is not None:
                return TaskResult(
                    input_file=str(archive_input.requested_path),
                    processed_file=str(archive_input.archive_path),
                    output_dir=None,
                    success=False,
                    error_code=ErrorCode.STEGO_NOT_FOUND.value,
                    message="No supported embedded archive found in video",
                )
            embedded = find_embedded_archives(stego_dir)
            if not embedded:
                return TaskResult(
                    input_file=str(archive_input.requested_path),
                    processed_file=str(archive_input.archive_path),
                    output_dir=None,
                    success=False,
                    error_code=ErrorCode.STEGO_NOT_FOUND.value,
                    message="No supported embedded archive found in video",
                )
            embedded_archive = embedded[0]
            yield TaskEvent(
                type="stage",
                stage="testing_embedded_password",
                current_file=str(archive_input.requested_path),
                file_index=index,
                file_count=file_count,
                message=f"Testing embedded archive: {embedded_archive.name}",
                details={"embedded_archive": str(embedded_archive)},
            )
            return (
                yield from _try_archive(
                    archive_path=embedded_archive,
                    input_file=archive_input.requested_path,
                    output_base=output_base,
                    task_temp=task_temp,
                    store=store,
                    app_config=app_config,
                    runner=runner,
                    request=request,
                    enable_compat=enable_compat,
                    index=index,
                    file_count=file_count,
                    testing_stage="testing_embedded_password",
                    extract_stage="extracting",
                    stego_embedded_file=str(embedded_archive),
                )
            )
        return result
    finally:
        shutil.rmtree(task_temp, ignore_errors=True)


def _try_archive(
    *,
    archive_path: Path,
    input_file: Path,
    output_base: Path,
    task_temp: Path,
    store: PasswordStore,
    app_config: AppConfig,
    runner: SevenZipRunner,
    request: ExtractRequest,
    enable_compat: bool,
    index: int,
    file_count: int,
    testing_stage: str,
    extract_stage: str,
    stego_embedded_file: str | None,
) -> Generator[TaskEvent, None, TaskResult]:
    candidates = build_candidates(store.active_records(), enable_encoding_compat=enable_compat)
    password_count = len(candidates)
    failed_codes: list[ErrorCode] = []
    matched: PasswordCandidate | None = None
    for password_index, candidate in enumerate(candidates, start=1):
        yield TaskEvent(
            type="password_test",
            stage=testing_stage,
            current_file=str(input_file),
            file_index=index,
            file_count=file_count,
            password_index=password_index,
            password_count=password_count,
            message=f"Testing password {password_index}/{password_count}",
            details={"candidate_source": candidate.label},
        )
        result = runner.test_archive(
            archive_path,
            password=candidate.password,
            cancel_token=request.cancel_token,
        )
        error = classify_result(result)
        if error is None:
            matched = candidate
            break
        if error == ErrorCode.CANCELED:
            raise KeyUnpackError(ErrorCode.CANCELED, "Canceled")
        failed_codes.append(error)

    if matched is None:
        error = _password_exhausted_error(failed_codes)
        return TaskResult(
            input_file=str(input_file),
            processed_file=str(archive_path),
            output_dir=None,
            success=False,
            error_code=error.value,
            message=_message_for_error(error),
            stego_embedded_file=stego_embedded_file,
        )

    extract_dir = task_temp / "extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    yield TaskEvent(
        type="stage",
        stage=extract_stage,
        current_file=str(input_file),
        file_index=index,
        file_count=file_count,
        message="Extracting archive",
    )
    extract_result = runner.extract_archive(
        archive_path,
        extract_dir,
        password=matched.password,
        cancel_token=request.cancel_token,
    )
    extract_error = classify_result(extract_result)
    if extract_error is not None:
        return TaskResult(
            input_file=str(input_file),
            processed_file=str(archive_path),
            output_dir=None,
            success=False,
            error_code=extract_error.value,
            message=_message_for_error(extract_error),
            stego_embedded_file=stego_embedded_file,
        )

    output_dir = move_extracted_tree(extract_dir, output_base, request.overwrite)
    task_result = TaskResult(
        input_file=str(input_file),
        processed_file=str(archive_path),
        output_dir=str(output_dir),
        success=True,
        success_password=matched.password,
        password_source=matched.source,
        original_password=matched.original_password,
        used_encoding_variant=matched.used_encoding_variant,
        encoding_variant_name=matched.encoding_variant_name,
        message=f"Extracted to {output_dir}",
        stego_embedded_file=stego_embedded_file,
    )
    store.mark_success(matched)
    append_success_log(app_config.paths, task_result, app_config.config)
    return task_result


def _password_exhausted_error(codes: list[ErrorCode]) -> ErrorCode:
    if not codes:
        return ErrorCode.UNKNOWN_ERROR
    if ErrorCode.VOLUME_MISSING in codes:
        return ErrorCode.VOLUME_MISSING
    if ErrorCode.PERMISSION_DENIED in codes:
        return ErrorCode.PERMISSION_DENIED
    if ErrorCode.ARCHIVE_CORRUPT in codes:
        return ErrorCode.ARCHIVE_CORRUPT
    if all(code == ErrorCode.UNSUPPORTED_FORMAT for code in codes):
        return ErrorCode.UNSUPPORTED_FORMAT
    if ErrorCode.BAD_PASSWORD in codes:
        return ErrorCode.PASSWORD_NOT_FOUND
    if ErrorCode.UNSUPPORTED_FORMAT in codes:
        return ErrorCode.UNSUPPORTED_FORMAT
    return codes[-1]


def _message_for_error(error: ErrorCode) -> str:
    messages = {
        ErrorCode.SEVENZIP_MISSING: "7-Zip executable not found",
        ErrorCode.PASSWORD_NOT_FOUND: "No candidate password succeeded",
        ErrorCode.BAD_PASSWORD: "Bad password",
        ErrorCode.ARCHIVE_CORRUPT: "Archive appears to be corrupt",
        ErrorCode.UNSUPPORTED_FORMAT: "Unsupported or non-archive file",
        ErrorCode.VOLUME_MISSING: "Archive volume is missing",
        ErrorCode.PERMISSION_DENIED: "Permission denied",
        ErrorCode.FILE_CONFLICT: "File conflict",
        ErrorCode.STEGO_NOT_FOUND: "No supported embedded archive found",
        ErrorCode.CANCELED: "Canceled",
        ErrorCode.UNKNOWN_ERROR: "Unknown extraction error",
    }
    return messages[error]


def _failure_event(
    current_file: Path | None,
    file_index: int | None,
    file_count: int | None,
    result: TaskResult,
) -> TaskEvent:
    return TaskEvent(
        type="failure",
        stage="failed",
        current_file=str(current_file) if current_file else None,
        file_index=file_index,
        file_count=file_count,
        message=result.message,
        error_code=result.error_code,
        result=result,
    )
