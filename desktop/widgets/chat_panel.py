# 答疑面板：就当前分节向 AI 提问，显示成对话记录
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.api.client import LearningClient
from desktop.workers import run_async


# 答疑面板
class ChatPanel(QWidget):
    # 自述状态变化，供外层显示进度
    busy_changed = pyqtSignal(bool)
    # 收到一次回答后抛出，供外层刷新统计
    answered = pyqtSignal()

    # 客户端由外面注入
    def __init__(self, client: LearningClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id: str = ''
        # 当前可提问的分节下标，None 表示不可提问
        self._section_index: int | None = None
        # 对话历史，只用于界面展示
        self._turns: list[tuple[str, str]] = []
        self._busy = False
        self._build_ui()

    # 搭控件树
    def _build_ui(self) -> None:
        header = QLabel('答疑')
        header.setStyleSheet('font-weight: bold;')

        self.hint = QLabel('生成本节资料后即可提问。')
        self.hint.setStyleSheet('color: #888;')
        self.hint.setWordWrap(True)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText('就本节内容提问…')
        self.input_edit.returnPressed.connect(self._on_send)

        self.send_btn = QPushButton('发送')
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._on_send)

        row = QHBoxLayout()
        row.addWidget(self.input_edit, 1)
        row.addWidget(self.send_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(header)
        layout.addWidget(self.hint)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(row)

    # 切换会话时重置
    def load_session(self, session: dict[str, Any]) -> None:
        self._session_id = str(session.get('session_id') or '')
        self._section_index = None
        self._turns = []
        self.transcript.clear()
        self._sync_enabled()

    # 切分节时更新可提问状态，backend_index 用于提示后端实际记录的分节
    def set_section(
        self,
        index: int,
        title: str,
        available: bool,
        backend_index: int | None = None
    ) -> None:
        self._section_index = index if available else None

        if not available:
            self.hint.setText(f'「{title}」尚未生成资料，先生成后即可提问。')
            self.hint.setStyleSheet('color: #d9534f;')
        elif backend_index is not None and backend_index != index:
            self.hint.setText(
                f'⚠️ 后端记录的提问分节是第 {backend_index + 1} 节，'
                f'与当前第 {index + 1} 节不同，回答可能不对题。'
            )
            self.hint.setStyleSheet('color: #d9822b;')
        else:
            self.hint.setText(f'当前提问针对：{title}')
            self.hint.setStyleSheet('color: #888;')

        self._sync_enabled()

    # 发送问题
    def _on_send(self) -> None:
        question = self.input_edit.text().strip()
        if not question or self._busy:
            return
        if not self._session_id or self._section_index is None:
            return

        self.input_edit.clear()
        self._turns.append(('user', question))
        self._render()
        self._set_busy(True)

        run_async(
            self._client.ask,
            self._session_id,
            question,
            on_ok=self._on_answered,
            on_error=self._on_error,
            on_finish=lambda: self._set_busy(False),
        )

    # 收到回答
    def _on_answered(self, data: Any) -> None:
        if not isinstance(data, dict):
            self._turns.append(('ai', '后端返回了未预期的结构。'))
            self._render()
            return

        answer = str(data.get('answer') or '（无内容）')
        point = str(data.get('point') or '')
        text = answer
        if point:
            text = f'{text}\n\n---\n\n*疑点：{point}*'
        self._turns.append(('ai', text))
        self._render()
        self.answered.emit()

    # 提问失败
    def _on_error(self, code: str, message: str) -> None:
        tips = {
            'artifact_not_found': '本节还没有资料，请先点「生成本节资料」。',
            'phase_guard_violation': '当前学习阶段不允许提问。',
            'client_offline': '后端未启动。',
            'client_timeout': '回答超时，可稍后重试。',
        }
        tip = tips.get(code, '')
        text = f'**提问失败**：{message}'
        if tip:
            text = f'{text}\n\n{tip}'
        self._turns.append(('ai', text))
        self._render()

    # 忙碌状态
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._sync_enabled()
        self.busy_changed.emit(busy)

    # 按状态刷新可用性
    def _sync_enabled(self) -> None:
        ready = bool(self._session_id) and self._section_index is not None and not self._busy
        self.input_edit.setEnabled(ready)
        self.send_btn.setEnabled(ready)
        self.send_btn.setText('思考中…' if self._busy else '发送')

    # 把问答记录渲染成 markdown
    def _render(self) -> None:
        if not self._turns:
            self.transcript.clear()
            return

        blocks: list[str] = []
        for role, text in self._turns:
            speaker = '我' if role == 'user' else 'AI'
            blocks.append(f'### {speaker}\n\n{text}')
        self.transcript.setMarkdown('\n\n'.join(blocks))

        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())