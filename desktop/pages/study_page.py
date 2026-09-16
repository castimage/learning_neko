# 学习页：左侧大纲列表 + 右侧资料正文
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.api.client import LearningClient
from desktop.workers import run_async


# 学习页
class StudyPage(QWidget):
    # 客户端由主窗口注入
    def __init__(self, client: LearningClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._session: dict[str, Any] = {}
        self._sections: list[dict[str, Any]] = []
        self._session_id: str = ''
        # 已生成资料的分节，避免重复烧 token
        self._generated: set[int] = set()
        # 缓存已生成的 bundle，切分节时无需重新请求
        self._bundles: dict[int, dict[str, Any]] = {}
        self._build_ui()
        self._connect_signals()

    # 搭控件树
    def _build_ui(self) -> None:
        self.topic_label = QLabel('尚未开始学习')
        self.topic_label.setStyleSheet('font-size: 16px; font-weight: bold;')

        self.phase_label = QLabel('')
        self.phase_label.setStyleSheet('color: #888;')

        self.material_box = QTextBrowser()
        self.material_box.setReadOnly(True)
        self.material_box.setOpenExternalLinks(True)
        self.material_box.setPlaceholderText('左侧选择分节后显示资料')

        self.outline_list = QListWidget()

        self.generate_btn = QPushButton('生成本节资料')
        self.generate_btn.setEnabled(False)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)      # 不确定进度：一直滚动
        self.progress.hide()

        self.status = QLabel('')
        self.status.setStyleSheet('color: #888;')
        self.status.setWordWrap(True)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.outline_list)
        splitter.addWidget(self.material_box)
        # 让两栏宽度可拖，初始比例 1:3
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(self.topic_label)
        header.addWidget(self.phase_label)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.generate_btn)
        toolbar.addWidget(self.progress, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        layout.addLayout(toolbar)
        layout.addWidget(self.status)

    # 所有连线集中在这里
    def _connect_signals(self) -> None:
        self.outline_list.currentRowChanged.connect(self._on_section_changed)
        self.generate_btn.clicked.connect(self._on_generate_clicked)

    # 用会话快照填充页面
    def load_session(self, session: dict[str, Any]) -> None:
        self._session = session
        self._session_id = session.get('session_id', '')
        self._sections = (session.get('outline') or {}).get('outline') or []
        self._generated.clear()
        self._bundles.clear()

        self.topic_label.setText(session.get('topic') or '未命名主题')
        self.phase_label.setText(
            f'阶段：{session.get("phase", "?")}'
            f'｜分节 {session.get("section_count", len(self._sections))} 个'
            f'｜可用操作：{"、".join(session.get("allowed_actions") or [])}'
        )

        # 批量填充时屏蔽信号，避免每加一项都触发一次回调
        self.outline_list.blockSignals(True)
        self.outline_list.clear()
        for index, item in enumerate(self._sections, start=1):
            self.outline_list.addItem(f'{index}. {item.get("title", "未命名")}')
        self.outline_list.blockSignals(False)

        memory = session.get('memory_context') or {}
        weak = memory.get('weak_points') or []
        if weak:
            self._show_markdown(
                '## 历史薄弱点\n\n本次会重点覆盖：\n\n- ' + '\n- '.join(weak)
            )
        else:
            self._show_markdown('请从左侧选择一个分节，然后点「生成本节资料」。')

        # 默认选中第一节，让右侧立刻有内容
        if self._sections:
            self.outline_list.setCurrentRow(0)

    # 切换分节时刷新右侧正文
    def _on_section_changed(self, row: int) -> None:
        if row < 0:                    # 清空列表时会发 -1，必须挡
            return
        if row >= len(self._sections):  # 防止索引越界
            return

        self._refresh_generate_btn(row)
        self._render_section(row)

    # 当前分节还没生成过就允许点生成
    def _refresh_generate_btn(self, row: int) -> None:
        has_session = bool(self._session_id)
        already = row in self._generated
        self.generate_btn.setEnabled(has_session and not already)
        self.generate_btn.setText('已生成（可重新生成）' if already else '生成本节资料')

    # 把 markdown 正文渲染进只读浏览器
    def _show_markdown(self, markdown: str) -> None:
        document = QTextDocument()
        # 样式表必须在 setMarkdown 之前设置才生效
        document.setDefaultStyleSheet(
            'pre, code { background-color: #2d2d2d; color: #ce9178; }'
            'a { color: #4daafc; }'
        )
        document.setMarkdown(markdown, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        self.material_box.setDocument(document)
        # 换文档后滚动条停在原位，手动回到顶部
        self.material_box.verticalScrollBar().setValue(0)

    # 渲染某个分节
    def _render_section(self, row: int) -> None:
        section = self._sections[row]
        title = section.get('title', '')
        bundle = self._bundles.get(row)
        markdown = (bundle or {}).get('material', '')

        if not markdown:
            goal = section.get('goal', '')
            keywords = '、'.join(section.get('keywords') or [])
            self._show_markdown(
                f'# {title}\n\n'
                f'- **目标**：{goal}\n'
                f'- **概念**：{keywords}\n\n'
                f'尚未生成资料，点击下方「生成本节资料」。'
            )
            return

        self._show_markdown(markdown)

    # 生成当前选中分节的资料
    def _on_generate_clicked(self) -> None:
        row = self.outline_list.currentRow()
        if row < 0 or row >= len(self._sections):
            QMessageBox.information(self, '请先选择分节', '请在左侧选择一个分节。')
            return

        if not self._session_id:
            QMessageBox.warning(self, '会话缺失', '当前没有有效的学习会话，请重新开始学习。')
            return

        title = self._sections[row].get('title', '')
        self.status.setText(f'正在生成「{title}」的学习资料…（可能需一到数分钟）')
        run_async(
            self._client.generate_material,
            self._session_id,
            row,
            on_start=self._lock,
            on_ok=lambda data, r=row: self._on_generated(r, data),
            on_error=self._on_generate_error,
            on_finish=self._unlock,
        )

    # 生成期间锁住按钮与列表，避免中途切分节造成状态错乱
    def _lock(self) -> None:
        self.generate_btn.setEnabled(False)
        self.outline_list.setEnabled(False)
        self.progress.show()

    # 生成结束恢复
    def _unlock(self) -> None:
        self.outline_list.setEnabled(True)
        self.progress.hide()
        self._refresh_generate_btn(self.outline_list.currentRow())

    # 生成成功：记录并展示
    def _on_generated(self, row: int, data: Any) -> None:
        if not isinstance(data, dict):
            self.status.setText('后端返回了未预期的结构。')
            return

        self._generated.add(row)
        bundle = data.get('bundle') or {}
        self._bundles[row] = bundle          # 缓存正文

        status = data.get('status', '')
        attempts = data.get('attempts', 0)
        chars = len(bundle.get('material') or '')
        examples = len(bundle.get('examples') or [])

        warn = '（校验未通过，已降级交付）' if status == 'accepted_with_warning' else ''
        self.status.setText(
            f'已生成「{self._sections[row].get("title", "")}」'
            f'｜正文 {chars} 字｜例题 {examples} 道｜校验 {attempts} 轮{warn}'
        )
        self._render_section(row)

    # 生成失败：按错误码给可操作提示
    def _on_generate_error(self, code: str, message: str) -> None:
        self.status.setText(f'生成失败：{message}')
        if code == 'client_offline':
            QMessageBox.critical(self, '后端未启动', f'{message}\n\n请先启动后端服务。')
            return
        if code == 'phase_guard_violation':
            QMessageBox.warning(
                self, '当前阶段不允许该操作',
                f'{message}\n\n会话状态可能已变化，建议重新读取会话快照。'
            )
            return
        if code == 'artifact_not_found':
            QMessageBox.warning(self, '产物缺失', f'{message}')
            return
        QMessageBox.critical(self, '生成资料失败', f'[{code}] {message}')