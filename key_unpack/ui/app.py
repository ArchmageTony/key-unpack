from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from key_unpack.core.config import AppConfig
from key_unpack.core.events import CancelToken, TaskEvent
from key_unpack.core.extract import ExtractRequest, extract_many
from key_unpack.core.passwords import PASSWORD_TYPES, PasswordRecord, PasswordStore


TYPE_LABELS = {
    "permanent": "永久",
    "one_time": "一次性",
    "temporary": "临时",
}

STAGE_LABELS = {
    "detecting": "检测文件",
    "testing_password": "测试密码",
    "testing_embedded_password": "测试内嵌压缩包密码",
    "extracting_stego": "提取隐写压缩包",
    "extracting": "正在解压",
    "completed": "完成",
    "failed": "失败",
}

ERROR_LABELS = {
    "sevenzip_missing": "7-Zip 不存在或路径错误",
    "password_not_found": "候选密码均失败",
    "bad_password": "密码错误",
    "archive_corrupt": "压缩包损坏",
    "unsupported_format": "格式不支持或不是压缩包",
    "volume_missing": "分卷缺失",
    "permission_denied": "权限不足",
    "file_conflict": "文件冲突",
    "stego_not_found": "未找到可处理的隐写压缩包",
    "canceled": "已取消",
    "unknown_error": "未知错误",
}


@dataclass
class TaskRow:
    row: int
    file_path: str


class ExtractWorker(QThread):
    event_received = Signal(object)
    finished_with_state = Signal(bool)

    def __init__(
        self,
        archives: list[str],
        *,
        output_dir: str | None,
        overwrite: str,
        data_dir: str | None,
        sevenzip_path: str | None,
        temp_dir: str | None,
        enable_encoding_compat: bool,
    ) -> None:
        super().__init__()
        self._archives = archives
        self._token = CancelToken()
        self._request = ExtractRequest(
            archives=archives,
            output_dir=output_dir,
            overwrite=overwrite,
            data_dir=data_dir,
            enable_stego=True,
            enable_password_encoding_compat=enable_encoding_compat,
            sevenzip_path=sevenzip_path,
            temp_dir=temp_dir,
            cancel_token=self._token,
        )

    def cancel(self) -> None:
        self._token.cancel()

    def run(self) -> None:
        failed = False
        try:
            for event in extract_many(self._request):
                if event.type == "failure":
                    failed = True
                self.event_received.emit(event)
                if self._token.canceled:
                    break
        finally:
            self.finished_with_state.emit(failed or self._token.canceled)


class DropArea(QFrame):
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        title = QLabel("拖入压缩包或隐写视频")
        title.setAlignment(Qt.AlignCenter)
        title.setObjectName("dropTitle")
        subtitle = QLabel("支持批量拖拽, 子卷会归一到主卷处理")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setObjectName("dropSubtitle")
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch(1)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and url.toLocalFile()
        ]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, *, data_dir: str | None = None) -> None:
        super().__init__()
        self.data_dir = data_dir
        self.config = AppConfig.load(data_dir)
        self.store = PasswordStore(self.config.paths)
        self.worker: ExtractWorker | None = None
        self.task_rows: dict[str, TaskRow] = {}
        self.setWindowTitle("Key Unpack")
        self.resize(920, 640)
        self._build_ui()
        self._load_settings()
        self.refresh_passwords()
        self.refresh_logs()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_extract_tab(), "解压")
        self.tabs.addTab(self._build_password_tab(), "密码")
        self.tabs.addTab(self._build_settings_tab(), "设置")
        self.tabs.addTab(self._build_logs_tab(), "日志")
        root_layout.addWidget(self.tabs)
        self.status_label = QLabel("")
        root_layout.addWidget(self.status_label)
        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow { background: #f5f6f8; }
            QWidget { font-size: 13px; }
            QTabWidget::pane, QGroupBox, QFrame#dropArea {
                border: 1px solid #d7dbe2;
                border-radius: 6px;
                background: #ffffff;
            }
            QGroupBox { margin-top: 10px; padding-top: 14px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QFrame#dropArea { min-height: 118px; }
            QLabel#dropTitle { font-size: 20px; font-weight: 600; color: #20242a; }
            QLabel#dropSubtitle { color: #667085; }
            QPushButton { min-height: 28px; padding: 4px 10px; }
            QTableWidget { background: #ffffff; alternate-background-color: #f8fafc; }
            """
        )

    def _build_extract_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.drop_area = DropArea()
        self.drop_area.files_dropped.connect(self.start_extract)
        layout.addWidget(self.drop_area)

        controls = QHBoxLayout()
        self.add_files_button = QPushButton("添加文件")
        self.add_files_button.clicked.connect(self.choose_archives)
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_extract)
        controls.addWidget(self.add_files_button)
        controls.addWidget(self.cancel_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        password_group = QGroupBox("快速存储剪贴板密码")
        password_layout = QHBoxLayout(password_group)
        self.quick_type_group = QButtonGroup(self)
        for index, password_type in enumerate(("permanent", "one_time", "temporary")):
            radio = QRadioButton(TYPE_LABELS[password_type])
            radio.setProperty("password_type", password_type)
            self.quick_type_group.addButton(radio)
            password_layout.addWidget(radio)
            if index == 1:
                radio.setChecked(True)
        self.store_clipboard_button = QPushButton("存储密码")
        self.store_clipboard_button.clicked.connect(self.store_clipboard_passwords)
        password_layout.addStretch(1)
        password_layout.addWidget(self.store_clipboard_button)
        layout.addWidget(password_group)

        self.task_table = QTableWidget(0, 6)
        self.task_table.setHorizontalHeaderLabels(["文件", "状态", "进度", "当前步骤", "结果", "输出目录"])
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.task_table.setAlternatingRowColors(True)
        layout.addWidget(self.task_table, 1)
        return page

    def _build_password_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        add_group = QGroupBox("新增密码")
        form = QGridLayout(add_group)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("输入一个密码")
        self.password_type_combo = QComboBox()
        for password_type in ("permanent", "one_time", "temporary"):
            self.password_type_combo.addItem(TYPE_LABELS[password_type], password_type)
        self.add_password_button = QPushButton("新增")
        self.add_password_button.clicked.connect(self.add_manual_password)
        self.import_clipboard_button = QPushButton("从剪贴板导入")
        self.import_clipboard_button.clicked.connect(self.import_passwords_from_clipboard)
        form.addWidget(QLabel("密码"), 0, 0)
        form.addWidget(self.password_input, 0, 1)
        form.addWidget(QLabel("类型"), 0, 2)
        form.addWidget(self.password_type_combo, 0, 3)
        form.addWidget(self.add_password_button, 0, 4)
        form.addWidget(self.import_clipboard_button, 0, 5)
        layout.addWidget(add_group)

        actions = QHBoxLayout()
        self.edit_password_button = QPushButton("修改选中")
        self.edit_password_button.clicked.connect(self.edit_selected_password)
        self.delete_password_button = QPushButton("删除选中")
        self.delete_password_button.clicked.connect(self.delete_selected_passwords)
        self.cleanup_password_button = QPushButton("清理过期临时密码")
        self.cleanup_password_button.clicked.connect(self.cleanup_expired_passwords)
        actions.addWidget(self.edit_password_button)
        actions.addWidget(self.delete_password_button)
        actions.addWidget(self.cleanup_password_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.password_table = QTableWidget(0, 6)
        self.password_table.setHorizontalHeaderLabels(["密码", "类型", "来源", "创建时间", "过期时间", "最后使用"])
        self.password_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 6):
            self.password_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.password_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.password_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.password_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.password_table.setAlternatingRowColors(True)
        layout.addWidget(self.password_table, 1)
        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form_group = QGroupBox("解压与数据设置")
        form = QFormLayout(form_group)

        self.sevenzip_path = QLineEdit()
        self.sevenzip_browse = QPushButton("选择")
        self.sevenzip_browse.clicked.connect(lambda: self.choose_file_for(self.sevenzip_path))
        form.addRow("7-Zip 路径", self._path_row(self.sevenzip_path, self.sevenzip_browse))

        self.output_strategy = QComboBox()
        self.output_strategy.addItem("压缩包同目录", "archive_dir")
        self.output_strategy.addItem("固定输出目录", "fixed")
        form.addRow("输出策略", self.output_strategy)

        self.output_dir = QLineEdit()
        self.output_browse = QPushButton("选择")
        self.output_browse.clicked.connect(lambda: self.choose_dir_for(self.output_dir))
        form.addRow("固定输出目录", self._path_row(self.output_dir, self.output_browse))

        self.temp_dir = QLineEdit()
        self.temp_browse = QPushButton("选择")
        self.temp_browse.clicked.connect(lambda: self.choose_dir_for(self.temp_dir))
        form.addRow("临时目录", self._path_row(self.temp_dir, self.temp_browse))

        self.overwrite_policy = QComboBox()
        for label, value in (("自动重命名", "rename"), ("跳过", "skip"), ("覆盖", "overwrite"), ("失败", "fail")):
            self.overwrite_policy.addItem(label, value)
        form.addRow("覆盖策略", self.overwrite_policy)

        self.default_password_type = QComboBox()
        for password_type in ("permanent", "one_time", "temporary"):
            self.default_password_type.addItem(TYPE_LABELS[password_type], password_type)
        form.addRow("默认密码类型", self.default_password_type)

        self.temporary_days = QSpinBox()
        self.temporary_days.setRange(1, 3650)
        form.addRow("临时密码有效期(天)", self.temporary_days)

        self.strip_imported = QCheckBox("导入时去除每行首尾空白")
        form.addRow("", self.strip_imported)

        self.encoding_compat = QCheckBox("启用中文密码编码兼容候选")
        form.addRow("", self.encoding_compat)

        self.log_success_password = QCheckBox("日志记录成功密码")
        form.addRow("", self.log_success_password)

        self.max_log_records = QSpinBox()
        self.max_log_records.setRange(0, 1_000_000)
        form.addRow("最大日志条数", self.max_log_records)

        self.max_log_bytes = QSpinBox()
        self.max_log_bytes.setRange(0, 2_147_483_647)
        form.addRow("最大日志字节", self.max_log_bytes)

        self.command_timeout = QSpinBox()
        self.command_timeout.setRange(0, 86_400)
        form.addRow("单次 7-Zip 超时(秒)", self.command_timeout)

        layout.addWidget(form_group)
        buttons = QHBoxLayout()
        self.save_settings_button = QPushButton("保存设置")
        self.save_settings_button.clicked.connect(self.save_settings)
        buttons.addWidget(self.save_settings_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _build_logs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        self.refresh_logs_button = QPushButton("刷新日志")
        self.refresh_logs_button.clicked.connect(self.refresh_logs)
        buttons.addWidget(self.refresh_logs_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.log_table = QTableWidget(0, 7)
        self.log_table.setHorizontalHeaderLabels(["时间", "文件", "密码", "原始密码", "来源", "输出目录", "隐写内嵌文件"])
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.log_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.log_table.setAlternatingRowColors(True)
        layout.addWidget(self.log_table, 1)
        return page

    def _path_row(self, line_edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit)
        layout.addWidget(button)
        return row

    def _load_settings(self) -> None:
        config = self.config.config
        local = self.config.local
        self.sevenzip_path.setText(str(local.get("sevenzip_path") or ""))
        self.output_dir.setText(str(local.get("output_dir") or ""))
        self.temp_dir.setText(str(local.get("temp_dir") or ""))
        self._set_combo_value(self.output_strategy, str(config.get("output_strategy", "archive_dir")))
        self._set_combo_value(self.overwrite_policy, str(config.get("overwrite", "rename")))
        self._set_combo_value(self.default_password_type, str(config.get("default_password_type", "one_time")))
        self.temporary_days.setValue(int(config.get("temporary_password_days", 7)))
        self.strip_imported.setChecked(bool(config.get("strip_imported_passwords", True)))
        self.encoding_compat.setChecked(bool(config.get("enable_password_encoding_compat", True)))
        self.log_success_password.setChecked(bool(config.get("log_success_password", True)))
        self.max_log_records.setValue(int(config.get("max_log_records", 1000)))
        self.max_log_bytes.setValue(int(config.get("max_log_bytes", 1048576)))
        self.command_timeout.setValue(int(config.get("command_timeout_seconds", 300)))
        self._set_quick_type(str(config.get("default_password_type", "one_time")))

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_quick_type(self, password_type: str) -> None:
        for button in self.quick_type_group.buttons():
            if button.property("password_type") == password_type:
                button.setChecked(True)
                return

    def choose_archives(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择压缩包或隐写视频")
        if paths:
            self.start_extract(paths)

    def start_extract(self, paths: list[str]) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.show_error("已有任务正在运行")
            return
        archives = [path for path in paths if Path(path).exists()]
        if not archives:
            self.show_error("没有可处理的文件")
            return
        self.reload_config()
        output_dir = None
        if self.config.config.get("output_strategy") == "fixed":
            output_dir = str(self.config.local.get("output_dir") or "")
            if not output_dir:
                self.show_error("固定输出目录未设置")
                return
        for path in archives:
            self._ensure_task_row(path)
        self.worker = ExtractWorker(
            archives,
            output_dir=output_dir,
            overwrite=str(self.config.config.get("overwrite", "rename")),
            data_dir=str(self.config.paths.data_dir),
            sevenzip_path=self._optional_text(self.sevenzip_path.text()) or self.config.sevenzip_path,
            temp_dir=self._optional_text(self.temp_dir.text()) or self.config.temp_dir,
            enable_encoding_compat=bool(self.config.config.get("enable_password_encoding_compat", True)),
        )
        self.worker.event_received.connect(self.handle_task_event)
        self.worker.finished_with_state.connect(self.extract_finished)
        self.set_running(True)
        self.worker.start()
        self.status_label.setText(f"已开始 {len(archives)} 个任务")

    def cancel_extract(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.status_label.setText("正在取消任务")
            self.cancel_button.setEnabled(False)

    def handle_task_event(self, event: TaskEvent) -> None:
        current = event.current_file or (event.result.input_file if event.result else "")
        if not current:
            return
        row = self._ensure_task_row(current).row
        status = "运行中"
        if event.type == "success":
            status = "成功"
        elif event.type == "failure":
            status = "失败"
        stage = STAGE_LABELS.get(event.stage or "", event.stage or event.type)
        progress = ""
        if event.file_index is not None and event.file_count is not None:
            progress = f"{event.file_index}/{event.file_count}"
        if event.password_index is not None and event.password_count is not None:
            progress = f"{progress} 密码 {event.password_index}/{event.password_count}".strip()
        message = event.message
        output_dir = ""
        if event.error_code:
            message = ERROR_LABELS.get(event.error_code, event.message)
        if event.result is not None:
            output_dir = event.result.output_dir or ""
            if event.result.message:
                message = ERROR_LABELS.get(event.result.error_code or "", event.result.message)
        self._set_task_items(row, status, progress, stage, message, output_dir)

    def extract_finished(self, failed: bool) -> None:
        self.set_running(False)
        self.reload_config()
        self.refresh_passwords()
        self.refresh_logs()
        self.status_label.setText("任务完成, 存在失败项" if failed else "任务完成")
        self.worker = None

    def set_running(self, running: bool) -> None:
        self.add_files_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.store_clipboard_button.setEnabled(not running)

    def _ensure_task_row(self, file_path: str) -> TaskRow:
        key = str(Path(file_path))
        existing = self.task_rows.get(key)
        if existing is not None:
            return existing
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        self.task_table.setItem(row, 0, QTableWidgetItem(Path(file_path).name))
        self.task_table.item(row, 0).setToolTip(file_path)
        self._set_task_items(row, "等待", "", "", "", "")
        task_row = TaskRow(row=row, file_path=file_path)
        self.task_rows[key] = task_row
        return task_row

    def _set_task_items(
        self,
        row: int,
        status: str,
        progress: str,
        stage: str,
        result: str,
        output_dir: str,
    ) -> None:
        for column, value in enumerate((status, progress, stage, result, output_dir), start=1):
            item = self.task_table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.task_table.setItem(row, column, item)
            item.setText(value)
            item.setToolTip(value)

    def selected_password_ids(self) -> list[str]:
        ids: list[str] = []
        for index in self.password_table.selectionModel().selectedRows():
            item = self.password_table.item(index.row(), 0)
            if item is not None:
                record_id = item.data(Qt.UserRole)
                if record_id:
                    ids.append(str(record_id))
        return ids

    def add_manual_password(self) -> None:
        password = self.password_input.text()
        if password == "":
            self.show_error("密码不能为空")
            return
        added = self._add_passwords(
            [password],
            password_type=str(self.password_type_combo.currentData()),
            source="ui",
            strip=False,
        )
        self.password_input.clear()
        self.status_label.setText(f"新增 {added} 条密码")

    def import_passwords_from_clipboard(self) -> None:
        password_type = str(self.password_type_combo.currentData())
        added = self._add_passwords_from_clipboard(password_type=password_type, source="clipboard")
        self.status_label.setText(f"导入 {added} 条密码")

    def store_clipboard_passwords(self) -> None:
        button = self.quick_type_group.checkedButton()
        password_type = str(button.property("password_type") if button else "one_time")
        added = self._add_passwords_from_clipboard(password_type=password_type, source="clipboard")
        self.status_label.setText(f"存储 {added} 条密码")

    def _add_passwords_from_clipboard(self, *, password_type: str, source: str) -> int:
        text = QApplication.clipboard().text()
        if text == "":
            self.show_error("剪贴板没有文本")
            return 0
        return self._add_passwords(text.splitlines(), password_type=password_type, source=source, strip=None)

    def _add_passwords(
        self,
        passwords: list[str],
        *,
        password_type: str,
        source: str,
        strip: bool | None,
    ) -> int:
        self.reload_config()
        added = self.store.add_passwords(
            passwords,
            password_type=password_type,
            source=source,
            strip=bool(self.config.config.get("strip_imported_passwords", True)) if strip is None else strip,
            temporary_days=int(self.config.config.get("temporary_password_days", 7)),
        )
        self.refresh_passwords()
        return added

    def edit_selected_password(self) -> None:
        ids = self.selected_password_ids()
        if len(ids) != 1:
            self.show_error("请选择一条密码")
            return
        records = self.store.load(include_expired=True)
        target = next((record for record in records if record.id == ids[0]), None)
        if target is None:
            self.show_error("选中密码不存在")
            return
        dialog = PasswordEditDialog(target, self)
        if dialog.exec() != PasswordEditDialog.Accepted:
            return
        new_password, new_type = dialog.values()
        if new_password == "":
            self.show_error("密码不能为空")
            return
        self.store.update_record(target.id, password=new_password, password_type=new_type)
        self.refresh_passwords()
        self.status_label.setText("密码已修改")

    def delete_selected_passwords(self) -> None:
        ids = set(self.selected_password_ids())
        if not ids:
            self.show_error("请选择要删除的密码")
            return
        if (
            QMessageBox.question(self, "确认删除", f"删除 {len(ids)} 条密码?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.store.delete_records(ids)
        self.refresh_passwords()
        self.status_label.setText(f"已删除 {len(ids)} 条密码")

    def cleanup_expired_passwords(self) -> None:
        removed = self.store.cleanup_expired()
        self.refresh_passwords()
        self.status_label.setText(f"清理 {removed} 条过期临时密码")

    def refresh_passwords(self) -> None:
        records = self.store.load(include_expired=True)
        self.password_table.setRowCount(0)
        for record in records:
            row = self.password_table.rowCount()
            self.password_table.insertRow(row)
            values = (
                record.password,
                TYPE_LABELS.get(record.type, record.type),
                record.source,
                record.created_at,
                record.expires_at or "",
                record.last_used_at or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, record.id)
                item.setToolTip(value)
                self.password_table.setItem(row, column, item)

    def save_settings(self) -> None:
        self.config.config["output_strategy"] = str(self.output_strategy.currentData())
        self.config.config["overwrite"] = str(self.overwrite_policy.currentData())
        self.config.config["default_password_type"] = str(self.default_password_type.currentData())
        self.config.config["temporary_password_days"] = self.temporary_days.value()
        self.config.config["strip_imported_passwords"] = self.strip_imported.isChecked()
        self.config.config["enable_password_encoding_compat"] = self.encoding_compat.isChecked()
        self.config.config["log_success_password"] = self.log_success_password.isChecked()
        self.config.config["max_log_records"] = self.max_log_records.value()
        self.config.config["max_log_bytes"] = self.max_log_bytes.value()
        self.config.config["command_timeout_seconds"] = self.command_timeout.value()
        self.config.local["sevenzip_path"] = self._optional_text(self.sevenzip_path.text())
        self.config.local["output_dir"] = self._optional_text(self.output_dir.text())
        self.config.local["temp_dir"] = self._optional_text(self.temp_dir.text())
        self.config.save_config()
        self.config.save_local()
        self._set_quick_type(str(self.config.config["default_password_type"]))
        self.status_label.setText("设置已保存")

    def refresh_logs(self) -> None:
        self.log_table.setRowCount(0)
        path = self.config.paths.extract_log_path
        if not path.exists():
            return
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    rows.append(data)
        for data in reversed(rows):
            row = self.log_table.rowCount()
            self.log_table.insertRow(row)
            values = (
                str(data.get("extracted_at") or ""),
                str(data.get("archive_name") or ""),
                str(data.get("password") or ""),
                str(data.get("original_password") or ""),
                str(data.get("password_source") or ""),
                str(data.get("output_dir") or ""),
                str(data.get("stego_embedded_file") or ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.log_table.setItem(row, column, item)

    def choose_file_for(self, line_edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if path:
            line_edit.setText(path)

    def choose_dir_for(self, line_edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(path)

    def reload_config(self) -> None:
        self.config = AppConfig.load(self.data_dir)
        self.store = PasswordStore(self.config.paths)

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Key Unpack", message)

    def _optional_text(self, value: str) -> str | None:
        stripped = value.strip()
        return stripped or None

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None and self.worker.isRunning():
            if (
                QMessageBox.question(self, "确认退出", "任务正在运行, 是否取消并退出?")
                != QMessageBox.StandardButton.Yes
            ):
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        event.accept()


class PasswordEditDialog(QDialog):
    def __init__(self, record: PasswordRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("修改密码")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.password_input = QLineEdit(record.password)
        self.type_combo = QComboBox()
        for password_type in PASSWORD_TYPES:
            self.type_combo.addItem(TYPE_LABELS.get(password_type, password_type), password_type)
        index = self.type_combo.findData(record.type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        form.addRow("密码", self.password_input)
        form.addRow("类型", self.type_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return self.password_input.text(), str(self.type_combo.currentData())


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    data_dir: str | None = None
    if args[:1] == ["--data-dir"]:
        if len(args) < 2:
            print("--data-dir requires a path", file=sys.stderr)
            return 2
        data_dir = args[1]
    app = QApplication(sys.argv[:1])
    window = MainWindow(data_dir=data_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
