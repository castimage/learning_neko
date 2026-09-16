# 学习页：左侧大纲列表，右侧资料正文与答疑
from __future__ import annotations

import re
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
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
from desktop.widgets.chat_panel import ChatPanel
from desktop.widgets.markdown_renderer import render_markdown

# 正文底色
CANVAS_COLOR = '#ffffff'

# 正文样式：白底黑字，代码块浅灰
_STYLE_SHEET = """
body { background-color: #ffffff; color: #24292f; }
p { margin: 6px 0; }
pre { background-color: #f6f8fa; padding: 8px; }
code { background-color: #f6f8fa; color: #953800; }
a { color: #0969da; }
table { border-collapse: collapse; margin: 8px 0; }
th, td { border: 1px solid #d0d7de; padding: 4px 8px; }
th { background-color: #f6f8fa; }
blockquote { border-left: 3px solid #d0d7de; margin: 8px 0; padding-left: 12px; color: #57606a; }
img { margin: 8px 0; }
hr { border: none; border-top: 1px solid #d0d7de; }
"""


# 学习页
class StudyPage(QWidget):
    # 会话进入「已学完」时对外抛出，供主窗口切到课后测验
    session_completed = pyqtSignal(object)

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
        # 控件自身背景，白底深字
        self.material_box.setStyleSheet(
            f'QTextBrowser {{ background-color: {CANVAS_COLOR}; color: #24292f; border: none; }}'
        )

        self.chat_panel = ChatPanel(self._client)

        self.outline_list = QListWidget()

        self.generate_btn = QPushButton('生成本节资料')
        self.generate_btn.setEnabled(False)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)      # 不确定进度：一直滚动
        self.progress.hide()

        self.status = QLabel('')
        self.status.setStyleSheet('color: #888;')
        self.status.setWordWrap(True)

        # 右侧上下两栏：上面看资料，下面提问
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(self.material_box)
        self.right_splitter.addWidget(self.chat_panel)
        self.right_splitter.setStretchFactor(0, 4)
        self.right_splitter.setStretchFactor(1, 2)

        self.complete_btn = QPushButton('完成学习')
        self.complete_btn.setEnabled(False)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.outline_list)
        splitter.addWidget(self.right_splitter)
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

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.generate_btn)
        toolbar.addWidget(self.complete_btn)
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
        self.complete_btn.clicked.connect(self._on_complete_clicked)

    # 暴露当前会话 id，供主窗口「重新读取」使用
    def current_session_id(self) -> str:
        return self._session_id

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

        # 只有 learning 阶段才允许切到完成
        allowed = set(session.get('allowed_actions') or [])
        self.complete_btn.setEnabled('complete' in allowed)
        self.complete_btn.setText('完成学习' if 'complete' in allowed else '已完成')

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

        self.chat_panel.load_session(session)

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

    # 把后端 markdown 渲染成 HTML 显示，公式与图都已内嵌
    def _show_markdown(self, markdown: str, media: list[dict[str, Any]] | None = None) -> None:
        html = render_markdown(markdown, media)

        document = QTextDocument()
        document.setDefaultStyleSheet(_STYLE_SHEET)
        document.setHtml(html)
        self.material_box.setDocument(document)
        self.material_box.verticalScrollBar().setValue(0)

    # 渲染某个分节
    def _render_section(self, row: int) -> None:
        section = self._sections[row]
        title = section.get('title', '')
        bundle = self._bundles.get(row)
        markdown = (bundle or {}).get('material', '')
        media = (bundle or {}).get('media') or []

        # 同步答疑面板：只有已生成资料的分节才能提问
        self.chat_panel.set_section(
            row,
            title,
            row in self._generated,
            backend_index=self._session.get('current_section_index')
        )

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

        # 正文里的 ![](media:xxx) 占位符就是配图位置，交由渲染器就地绘图
        text = _append_legend(markdown, bundle or {})
        self._show_markdown(text, media)

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
        self.outline_list.setEnabled(False)
        self.progress.show()

    # 生成结束恢复
    def _unlock(self) -> None:
        self.outline_list.setEnabled(True)
        self.progress.hide()
        self._refresh_generate_btn(self.outline_list.currentRow())
        # 完成按钮的状态由阶段决定，重新读会话
        allowed = set(self._session.get('allowed_actions') or [])
        self.complete_btn.setEnabled('complete' in allowed)

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
        media_count = len(bundle.get('media') or [])
        nodes = len((bundle.get('visualization') or {}).get('nodes') or [])

        warn = '（校验未通过，已降级交付）' if status == 'accepted_with_warning' else ''
        self.status.setText(
            f'已生成「{self._sections[row].get("title", "")}」'
            f'｜正文 {chars} 字｜例题 {examples} 道｜配图 {media_count} 件'
            f'｜关系图 {nodes} 节点｜校验 {attempts} 轮{warn}'
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

    # 切换到学习完毕，需先确认，因为不可回退
    def _on_complete_clicked(self) -> None:
        if not self._session_id:
            return

        answer = QMessageBox.question(
            self, '确认完成学习',
            '标记为学完后就无法回到「学习中」阶段，确定继续吗？'
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return

        self.status.setText('正在切换到「已学完」…')
        run_async(
            self._client.complete,
            self._session_id,
            on_start=self._lock,
            on_ok=self._on_completed,
            on_error=self._on_complete_error,
            on_finish=self._unlock,
        )

    # 切换成功：刷新页面并把会话交给主窗口
    def _on_completed(self, data: Any) -> None:
        if not isinstance(data, dict):
            self.status.setText('后端返回了未预期的结构。')
            return
        self.load_session(data)
        self.status.setText('已标记为学完，可以去做课后测验了。')
        self.session_completed.emit(data)

    # 切换失败
    def _on_complete_error(self, code: str, message: str) -> None:
        self.status.setText(f'切换失败：{message}')
        if code == 'already_completed':
            QMessageBox.information(self, '已经学完', f'{message}\n\n可以直接去做课后测验。')
            return
        if code == 'phase_guard_violation':
            QMessageBox.warning(self, '当前阶段不允许该操作', f'{message}')
            return
        if code == 'client_offline':
            QMessageBox.critical(self, '后端未启动', f'{message}\n\n请先启动后端服务。')
            return
        QMessageBox.critical(self, '切换失败', f'[{code}] {message}')



# 把关系图的节点说明整理成附注，追加到正文末尾
def _append_legend(markdown: str, bundle: dict[str, Any]) -> str:
    visualization = bundle.get('visualization')
    if not isinstance(visualization, dict):
        return markdown

    nodes = [n for n in (visualization.get('nodes') or []) if isinstance(n, dict)]
    if not nodes:
        return markdown

    # 正文里已经画过图，这里只补节点释义，避免图重复
    lines = ['', '---', '', '### 图中概念释义', '']
    for node in nodes:
        label = str(node.get('label') or '').strip()
        detail = str(node.get('detail') or '').strip()
        if not label:
            continue
        lines.append(f'- **{label}**：{detail}' if detail else f'- **{label}**')

    notes = [str(n) for n in (visualization.get('notes') or []) if str(n).strip()]
    if notes:
        lines.extend(['', '### 补充说明', ''])
        lines.extend(f'- {note}' for note in notes)

    return markdown + '\n'.join(lines) + '\n'