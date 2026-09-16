# 起始页：选择资料文件与需求，提交后由主窗口切到学习页
from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop.api.client import LearningClient
from desktop.documents import DocumentError, SUPPORTED_SUFFIXES, extract_text
from desktop.widgets.file_drop import FileDropArea
from desktop.workers import run_async

# 资料超过这个长度就提醒用户，避免一次提交过大正文
LENGTH_WARNING = 20000
# 单文件读取上限，超过直接拒绝，避免把超大文件读进内存
MAX_FILE_BYTES = 20_000_000


# 起始页控件
class StartPage(QWidget):
    # 会话创建成功后对外抛出，携带后端返回的会话快照
    session_started = pyqtSignal(object)

    # 客户端从外面注入，页面本身不创建依赖
    def __init__(self, client: LearningClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        # 已选文件解析出的正文
        self._document = ''
        self._build_ui()
        self._connect_signals()

    # 搭控件树
    def _build_ui(self) -> None:
        title = QLabel('开始一次学习')
        title.setStyleSheet('font-size: 20px; font-weight: bold;')

        hint = QLabel('选择或拖入一份资料文件，再写一句你的学习需求。')
        hint.setStyleSheet('color: #888;')

        self.drop_area = FileDropArea()

        self.file_info = QLabel('尚未选择资料文件')
        self.file_info.setStyleSheet('color: #888;')
        self.file_info.setWordWrap(True)

        self.request_edit = QLineEdit()
        self.request_edit.setPlaceholderText('例如：我是初学者，想理解条件概率和贝叶斯公式')

        self.start_btn = QPushButton('开始学习')
        self.start_btn.setMinimumHeight(36)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)      # 不确定进度：一直滚动
        self.progress.hide()

        self.status = QLabel('')
        self.status.setStyleSheet('color: #888;')
        self.status.setWordWrap(True)

        request_row = QHBoxLayout()
        request_row.addWidget(QLabel('学习需求：'))
        request_row.addWidget(self.request_edit, 1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.file_info, 1)
        bottom_row.addWidget(self.start_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(QLabel('学习资料：'))
        layout.addWidget(self.drop_area)
        layout.addLayout(request_row)
        layout.addWidget(self.progress)
        layout.addLayout(bottom_row)
        layout.addWidget(self.status)
        layout.addStretch(1)

    # 所有连线集中在这里
    def _connect_signals(self) -> None:
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.drop_area.file_selected.connect(self._on_file_selected)

    # 读取并校验选中的资料文件
    def _on_file_selected(self, path: str) -> None:
        source = Path(path)
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            QMessageBox.warning(
                self, '暂不支持该格式',
                '目前支持 PDF、Word(.docx)、EPUB 以及 txt、md 等文本资料。'
            )
            return

        try:
            size = source.stat().st_size
        except OSError as exc:
            QMessageBox.critical(self, '无法读取文件', f'{source.name}：{exc}')
            return

        if size > MAX_FILE_BYTES:
            QMessageBox.warning(
                self, '文件过大',
                f'{source.name} 有 {size / 1024 / 1024:.1f} MB，'
                f'超过 {MAX_FILE_BYTES // 1024 // 1024} MB 上限，请先精简资料。'
            )
            return

        try:
            document = extract_text(source).strip()
        except (DocumentError, OSError) as exc:
            QMessageBox.critical(self, '无法读取文件', f'{source.name}：{exc}')
            return

        if not document:
            QMessageBox.warning(self, '文件是空的', f'{source.name} 里没有可读取的文字。')
            return

        self._document = document
        self.drop_area.show_file(source.name, len(document))

        too_long = len(document) > LENGTH_WARNING
        self.file_info.setText(
            f'已选择：{source.name}｜{len(document)} 字'
            + ('（较长，生成会慢一些）' if too_long else '')
        )
        self.file_info.setStyleSheet('color: #d9534f;' if too_long else 'color: #888;')

    # 校验输入并提交
    def _on_start_clicked(self) -> None:
        document = self._document
        request = self.request_edit.text().strip()

        if not document:
            QMessageBox.information(self, '还差一步', '请先选择或拖入资料文件。')
            return

        if not request:
            QMessageBox.information(self, '还差一步', '请写一句学习需求，方便生成合适的大纲。')
            return

        if len(document) > LENGTH_WARNING:
            answer = QMessageBox.question(
                self,
                '资料较长',
                f'当前资料 {len(document)} 字，生成会比较慢（可能数分钟）。\n确定继续吗？'
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return

        self.status.setText('正在生成大纲，请稍候…（首次可能需一到数分钟）')
        run_async(
            self._client.start_session,
            document,
            request,
            on_start=self._lock,
            on_ok=self._on_started,
            on_error=self._on_error,
            on_finish=self._unlock,
        )

    # 提交期间锁住输入区，防止重复提交
    def _lock(self) -> None:
        self.start_btn.setEnabled(False)
        self.start_btn.setText('生成中…')
        self.drop_area.setEnabled(False)
        self.request_edit.setReadOnly(True)
        self.progress.show()

    # 提交结束恢复可编辑
    def _unlock(self) -> None:
        self.start_btn.setEnabled(True)
        self.start_btn.setText('开始学习')
        self.drop_area.setEnabled(True)
        self.request_edit.setReadOnly(False)
        self.progress.hide()

    # 成功：把会话交给主窗口
    def _on_started(self, data: Any) -> None:
        if not isinstance(data, dict):
            self.status.setText('后端返回了未预期的结构。')
            return
        section_count = data.get('section_count', 0)
        memory = data.get('memory_context') or {}
        revisits = memory.get('study_count', 0)
        extra = f'（第 {revisits + 1} 次学习该主题）' if revisits else ''
        self.status.setText(f'大纲已生成：{data.get("topic", "")}，共 {section_count} 个分节{extra}')
        self.session_started.emit(data)

    # 失败：区分「后端没起」「没配 key」「其他」，给出可操作的提示
    def _on_error(self, code: str, message: str) -> None:
        self.status.setText(f'失败：{message}')
        if code == 'client_offline':
            QMessageBox.critical(
                self, '后端未启动',
                f'{message}\n\n请先在另一个终端运行：\npython main.py'
            )
            return
        if code == 'provider_not_configured':
            QMessageBox.critical(
                self, '模型厂商未配置',
                f'{message}\n\n请在项目根目录的 .env 中填写对应的 API Key。'
            )
            return
        QMessageBox.critical(self, '生成大纲失败', f'[{code}] {message}')

    # 供主窗口在「新建」时清空页面状态
    def reset(self) -> None:
        self.status.setText('')
        self._document = ''
        self.drop_area.clear_file()
        self.request_edit.clear()
        self.file_info.setText('尚未选择资料文件')
        self.file_info.setStyleSheet('color: #888;')
