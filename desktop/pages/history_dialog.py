# 历史会话对话框：从服务端列出已有会话，选中后由调用方决定怎么打开
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.api.client import LearningClient
from desktop.workers import run_async

# 一次拉取的最大条数，后端上限为 200
PAGE_LIMIT = 50


# 历史会话对话框
class HistoryDialog(QDialog):
    # 客户端由外面注入
    def __init__(self, client: LearningClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        # 选中并确认的会话 id，取消时保持为 None
        self.selected_session_id: str | None = None
        self._summaries: list[dict[str, Any]] = []
        # 每次加载递增，用来丢弃过期请求的结果
        self._load_token = 0
        self.setWindowTitle('打开历史会话')
        self.resize(760, 440)
        self._build_ui()
        self._load()

    # 搭控件树
    def _build_ui(self) -> None:
        self.hint = QLabel('选择一个会话继续学习。数据以服务端为准。')
        self.hint.setStyleSheet('color: #888;')

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['主题', '阶段', '进度', '最近更新'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._on_confirm)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)      # 不确定进度：一直滚动
        self.progress.hide()

        self.refresh_btn = QPushButton('刷新')

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Open).setText('打开')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self._on_confirm)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(self.hint)
        layout.addWidget(self.progress)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.buttons)

        self.refresh_btn.clicked.connect(self._load)

    # 向后端要最近会话列表
    def _load(self) -> None:
        self._load_token += 1
        token = self._load_token
        self.hint.setText('正在读取…')
        run_async(
            self._client.list_sessions,
            PAGE_LIMIT,
            on_start=self._lock,
            on_ok=lambda data, t=token: self._on_loaded(data, t),
            on_error=lambda code, message, t=token: self._on_error(code, message, t),
            on_finish=self._unlock,
        )

    # 加载期间禁用刷新
    def _lock(self) -> None:
        self.refresh_btn.setEnabled(False)
        self.progress.show()

    # 加载结束恢复
    def _unlock(self) -> None:
        self.refresh_btn.setEnabled(True)
        self.progress.hide()

    # 填充表格
    def _on_loaded(self, data: Any, token: int) -> None:
        if token != self._load_token:
            return
        if not isinstance(data, list):
            self.hint.setText('后端返回了未预期的结构。')
            return

        self._summaries = [item for item in data if isinstance(item, dict)]
        self.table.setRowCount(len(self._summaries))

        for row, item in enumerate(self._summaries):
            topic = item.get('topic') or '未命名主题'
            phase = str(item.get('phase', '?'))
            updated = _format_time(item.get('updated_at'))

            self.table.setItem(row, 0, QTableWidgetItem(topic))
            self.table.setItem(row, 1, QTableWidgetItem(_phase_label(phase)))
            # 先占位，稍后用快照里的已生成数覆盖
            self.table.setItem(row, 2, QTableWidgetItem('…'))
            self.table.setItem(row, 3, QTableWidgetItem(updated))

        self.table.resizeColumnsToContents()
        # 主题列给足宽度，其余按内容自适应
        self.table.setColumnWidth(0, max(240, self.table.columnWidth(0)))

        if self._summaries:
            self.table.selectRow(0)
            self.hint.setText(f'共 {len(self._summaries)} 个会话，双击或点「打开」继续。')
            self._load_progress(token)
        else:
            self.hint.setText('还没有任何会话，先在起始页开始一次学习吧。')

    # 逐条拉快照，用已生成资料的分节数覆盖进度列
    def _load_progress(self, token: int) -> None:
        for row, item in enumerate(self._summaries):
            session_id = item.get('session_id')
            if not session_id:
                continue
            run_async(
                self._client.snapshot,
                session_id,
                on_ok=lambda data, r=row, t=token: self._on_progress(r, data, t),
                on_error=lambda code, message, r=row, t=token: None,
            )

    # 用快照里的已生成数更新某一行
    def _on_progress(self, row: int, data: Any, token: int) -> None:
        if token != self._load_token or not isinstance(data, dict):
            return
        if row < 0 or row >= self.table.rowCount():
            return

        generated = data.get('generated_sections') or []
        total = int(data.get('section_count') or 0)
        self.table.setItem(row, 2, QTableWidgetItem(f'{len(generated)}/{total}'))
        self.table.resizeColumnToContents(2)

    # 读取失败
    def _on_error(self, code: str, message: str, token: int) -> None:
        if token != self._load_token:
            return
        self.hint.setText(f'读取失败：{message}')
        if code == 'client_offline':
            QMessageBox.critical(self, '后端未启动', f'{message}\n\n请先启动后端服务。')
            return
        QMessageBox.critical(self, '读取会话列表失败', f'[{code}] {message}')

    # 确认选择
    def _on_confirm(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._summaries):
            QMessageBox.information(self, '请先选择', '请先在列表里选择一个会话。')
            return
        self.selected_session_id = self._summaries[row].get('session_id')
        self.accept()


# 把后端的阶段标识翻成中文，未知值原样显示
def _phase_label(phase: str) -> str:
    return {'learning': '学习中', 'completed': '已学完'}.get(phase, phase)


# 把 iso 时间改成易读形式，解析失败就原样截断
def _format_time(raw: Any) -> str:
    if not isinstance(raw, str) or not raw:
        return '-'
    return raw.replace('T', ' ')[:16]