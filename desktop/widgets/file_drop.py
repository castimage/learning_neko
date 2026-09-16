# 文件拖放区：点击打开文件选择，或把文件拖进来，选中后抛出本地路径
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PyQt6.QtWidgets import QFileDialog, QFrame, QLabel, QVBoxLayout

# 打开对话框时的文件类型过滤
FILE_FILTER = (
    '资料文件 (*.pdf *.docx *.epub *.txt *.md *.markdown *.text *.rst *.csv *.log);;'
    '所有文件 (*)'
)

# 普通态与拖拽悬停态的外观
_IDLE_STYLE = (
    'QFrame#fileDropArea { border: 2px dashed #c0c7d0; border-radius: 10px;'
    ' background-color: #fafbfc; }'
)
_ACTIVE_STYLE = (
    'QFrame#fileDropArea { border: 2px dashed #0969da; border-radius: 10px;'
    ' background-color: #f0f6ff; }'
)
_PROMPT = '把资料文件拖到这里，或点击选择文件'
_PROMPT_DETAIL = '支持 PDF、Word、EPUB 及 txt / md 等文本文件'


# 可点击、可拖放的文件选择区
class FileDropArea(QFrame):
    # 选中一个本地文件后抛出其路径
    file_selected = pyqtSignal(str)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName('fileDropArea')
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(140)
        self.setStyleSheet(_IDLE_STYLE)
        self._build_ui()

    # 搭控件树，标签对鼠标透明，点击与拖放都落到本控件
    def _build_ui(self) -> None:
        self.title = QLabel(_PROMPT)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet('color: #24292f; font-size: 14px;')
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.detail = QLabel(_PROMPT_DETAIL)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setStyleSheet('color: #888;')
        self.detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)
        layout.addStretch(1)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addStretch(1)

    # 外部选中文件后回显文件名与字数
    def show_file(self, name: str, chars: int) -> None:
        self.title.setText(name)
        self.detail.setText(f'已读取 {chars} 字')

    # 清空回显，恢复初始提示
    def clear_file(self) -> None:
        self.title.setText(_PROMPT)
        self.detail.setText(_PROMPT_DETAIL)

    # 拖入内容里是否带本地文件
    def _has_local_file(self, event: Any) -> bool:
        mime = event.mimeData()
        return mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())

    # 拖入时目标合法性决定是否接受
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._has_local_file(event):
            event.acceptProposedAction()
            self.setStyleSheet(_ACTIVE_STYLE)

    # 拖动途中持续判断
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._has_local_file(event):
            event.acceptProposedAction()

    # 离开时恢复外观
    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.setStyleSheet(_IDLE_STYLE)

    # 放下时取第一个本地文件
    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet(_IDLE_STYLE)
        if not self._has_local_file(event):
            return

        for url in event.mimeData().urls():
            if url.isLocalFile():
                event.acceptProposedAction()
                self.file_selected.emit(url.toLocalFile())
                return

    # 点击打开文件选择对话框
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            path, _ = QFileDialog.getOpenFileName(self, '选择学习资料', '', FILE_FILTER)
            if path:
                self.file_selected.emit(path)
            return
        super().mousePressEvent(event)
