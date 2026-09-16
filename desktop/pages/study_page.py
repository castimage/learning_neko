# 学习页：左侧大纲列表，右侧资料正文与答疑
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QTextDocument
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
from desktop.widgets.graph_view import ConceptGraphView
from desktop.widgets.markdown_renderer import render_markdown
from desktop.widgets.media_assets import image_to_data_url, visualization_to_image
from desktop.widgets.quiz_panel import QuizPanel

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
        # 正在从后端回填的分节，避免重复发请求
        self._loading: set[int] = set()
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
        self.material_box.setOpenExternalLinks(False)   # 链接改由 anchorClicked 统一处理
        self.material_box.setPlaceholderText('左侧选择分节后显示资料')
        # 控件自身背景，白底深字
        self.material_box.setStyleSheet(
            f'QTextBrowser {{ background-color: {CANVAS_COLOR}; color: #24292f; border: none; }}'
        )

        self.outline_list = QListWidget()
        self.outline_toggle_btn = QPushButton('折叠大纲')
        self.outline_toggle_btn.setCheckable(True)

        # 三类详情内容，各自放在可开关的右栏里
        self.graph_view = ConceptGraphView()
        self.chat_panel = ChatPanel(self._client)
        self.examples_panel = QuizPanel(
            self._client.check_examples,
            empty_hint='生成本节资料后即可练习本节例题。',
            submit_text='提交答案'
        )
        self.examples_regen_btn = QPushButton('重新生成本节资料后可作答')
        self.examples_regen_btn.hide()

        examples_body = QWidget()
        examples_layout = QVBoxLayout(examples_body)
        examples_layout.setContentsMargins(0, 0, 0, 0)
        examples_layout.setSpacing(6)
        examples_layout.addWidget(self.examples_regen_btn)
        examples_layout.addWidget(self.examples_panel, 1)

        self.graph_panel, graph_close = self._build_detail_column(
            '知识点关系图', self.graph_view, hint='滚轮缩放 · 拖动节点调整 · 空白处拖动平移'
        )
        self.qa_column, qa_close = self._build_detail_column('答疑', self.chat_panel)
        self.examples_column, examples_close = self._build_detail_column('例题', examples_body)

        self.qa_toggle_btn = QPushButton('答疑')
        self.examples_toggle_btn = QPushButton('例题')
        self.graph_toggle_btn = QPushButton('关系图')
        for button in (self.qa_toggle_btn, self.examples_toggle_btn, self.graph_toggle_btn):
            button.setCheckable(True)

        self._bind_column(self.qa_toggle_btn, self.qa_column, qa_close)
        self._bind_column(self.examples_toggle_btn, self.examples_column, examples_close)
        self._bind_column(self.graph_toggle_btn, self.graph_panel, graph_close)

        self.generate_btn = QPushButton('生成本节资料')
        self.generate_btn.setEnabled(False)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)      # 不确定进度：一直滚动
        self.progress.hide()

        self.status = QLabel('')
        self.status.setStyleSheet('color: #888;')
        self.status.setWordWrap(True)

        self.complete_btn = QPushButton('完成学习')
        self.complete_btn.setEnabled(False)

        # 主区横向：大纲 / 正文 / 三栏可开关的详情
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.outline_list)
        self.main_splitter.addWidget(self.material_box)
        self.main_splitter.addWidget(self.graph_panel)
        self.main_splitter.addWidget(self.qa_column)
        self.main_splitter.addWidget(self.examples_column)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 3)

        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(self.topic_label)
        header.addWidget(self.phase_label)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.outline_toggle_btn)
        toolbar.addWidget(self.generate_btn)
        toolbar.addWidget(self.complete_btn)
        toolbar.addWidget(self.progress, 1)
        toolbar.addWidget(self.qa_toggle_btn)
        toolbar.addWidget(self.examples_toggle_btn)
        toolbar.addWidget(self.graph_toggle_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self.main_splitter, 1)
        layout.addLayout(toolbar)
        layout.addWidget(self.status)

    # 所有连线集中在这里
    def _connect_signals(self) -> None:
        self.outline_list.currentRowChanged.connect(self._on_section_changed)
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        self.examples_regen_btn.clicked.connect(self._on_generate_clicked)
        self.complete_btn.clicked.connect(self._on_complete_clicked)
        self.outline_toggle_btn.toggled.connect(self._on_toggle_outline)
        self.material_box.anchorClicked.connect(self._on_material_link)

    # 折叠或展开左侧大纲，给正文腾出空间
    def _on_toggle_outline(self, collapsed: bool) -> None:
        self.outline_list.setVisible(not collapsed)
        self.outline_toggle_btn.setText('展开大纲' if collapsed else '折叠大纲')

    # 右栏：标题 + 可选提示 + 关闭按钮，下面是内容；默认收起
    def _build_detail_column(
        self,
        title: str,
        content: QWidget,
        *,
        hint: str = ''
    ) -> tuple[QWidget, QPushButton]:
        title_label = QLabel(title)
        title_label.setStyleSheet('font-weight: bold;')

        head = QHBoxLayout()
        head.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setStyleSheet('color: #888;')
            head.addWidget(hint_label)
        head.addStretch(1)

        close_btn = QPushButton('关闭')
        head.addWidget(close_btn)

        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(head)
        layout.addWidget(content, 1)
        column.hide()
        return column, close_btn

    # 开关按钮与右栏绑定：勾选展开、取消收起，关闭按钮同步取消勾选
    def _bind_column(self, button: QPushButton, column: QWidget, close_btn: QPushButton) -> None:
        button.toggled.connect(lambda checked, c=column: self._toggle_column(c, checked))
        close_btn.clicked.connect(lambda: button.setChecked(False))

    # 展开或收起一栏，展开时重新分配各栏宽度
    def _toggle_column(self, column: QWidget, visible: bool) -> None:
        column.setVisible(visible)
        if visible:
            self._rebalance_columns()

    # 按可见性给各栏分配宽度，大纲窄一些
    def _rebalance_columns(self) -> None:
        width = self.main_splitter.width()
        if width <= 0:
            return

        weights = [self._column_weight(self.main_splitter.widget(i))
                   for i in range(self.main_splitter.count())]
        total = sum(weights) or 1.0
        self.main_splitter.setSizes([int(width * weight / total) for weight in weights])

    # 大纲占比较小，详情栏与正文等权
    def _column_weight(self, widget: QWidget) -> float:
        if not widget.isVisible():
            return 0.0
        if widget is self.outline_list:
            return 0.5
        return 1.0

    # 正文链接：graph 协议展开关系图栏，其余交给系统打开
    def _on_material_link(self, url: QUrl) -> None:
        if url.scheme() == 'graph':
            self.graph_toggle_btn.setChecked(True)
            return
        QDesktopServices.openUrl(url)

    # 暴露当前会话 id，供主窗口「重新读取」使用
    def current_session_id(self) -> str:
        return self._session_id

    # 用会话快照填充页面
    def load_session(self, session: dict[str, Any]) -> None:
        self._session = session
        self._session_id = session.get('session_id', '')
        self._sections = (session.get('outline') or {}).get('outline') or []
        # 后端快照会带上已生成资料的分节下标，据此标记，切分节时按需回填正文
        self._generated = {
            item for item in (session.get('generated_sections') or [])
            if isinstance(item, int)
        }
        self._bundles.clear()
        self._loading.clear()

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
        self._ensure_material_loaded(row)

    # 只要有会话就允许生成；已生成过的用文案提示这是重新生成
    def _refresh_generate_btn(self, row: int) -> None:
        has_session = bool(self._session_id)
        already = row in self._generated
        self.generate_btn.setEnabled(has_session)
        self.generate_btn.setText('重新生成本节资料' if already else '生成本节资料')

    # 把后端 markdown 渲染成 HTML 显示，公式与图都已内嵌
    def _show_markdown(
        self,
        markdown: str,
        media: list[dict[str, Any]] | None = None,
        visualization: dict[str, Any] | None = None
    ) -> None:
        html = render_markdown(markdown, media, visualization)

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
        self._sync_examples(row)
        # 交互图与静态图同源，切分节时一起刷新
        self.graph_view.render_graph((bundle or {}).get('visualization') or {})

        if not markdown:
            # 后端标记已生成但正文还没回填，先给个过场提示
            if row in self._loading:
                self._show_markdown(f'# {title}\n\n正在读取已生成资料…')
                return
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
        self._show_markdown(text, media, (bundle or {}).get('visualization'))

    # 同步例题面板：只有后端当前分节能提交判定，其它分节只读展示
    def _sync_examples(self, row: int) -> None:
        bundle = self._bundles.get(row) or {}
        examples = bundle.get('examples') or []
        self.examples_panel.load_exercises(self._session_id, examples)

        gradeable = row in self._generated and self._session.get('current_section_index') == row
        self.examples_panel.set_gradeable(gradeable)
        self.examples_regen_btn.setVisible(bool(examples) and not gradeable)

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
        # 后端生成时会把当前分节指针移到本节，本地同步后例题才能立刻判定
        self._session['current_section_index'] = row
        self._render_section(row)

    # 该分节若标记为已生成但正文未在内存，按需从后端回填一次
    def _ensure_material_loaded(self, row: int) -> None:
        if not self._session_id:
            return
        if row not in self._generated or row in self._bundles or row in self._loading:
            return

        self._loading.add(row)
        # 当前正在看这一节，先把过场提示显示出来
        if self.outline_list.currentRow() == row:
            self._render_section(row)

        run_async(
            self._client.read_material,
            self._session_id,
            row,
            on_ok=lambda data, r=row: self._on_material_loaded(r, data),
            on_error=lambda code, message, r=row: self._on_material_load_error(r, code, message),
        )

    # 回填成功：缓存正文并按当前选中状态渲染
    def _on_material_loaded(self, row: int, data: Any) -> None:
        self._loading.discard(row)
        if not isinstance(data, dict):
            self.status.setText('后端返回了未预期的结构。')
            return

        self._generated.add(row)
        self._bundles[row] = data.get('bundle') or {}
        if self.outline_list.currentRow() == row:
            self._render_section(row)

    # 回填失败：产物确已不在就撤掉标记，否则保留供下次重试
    def _on_material_load_error(self, row: int, code: str, message: str) -> None:
        self._loading.discard(row)
        if code == 'artifact_not_found':
            self._generated.discard(row)
        self.status.setText(f'读取已生成资料失败：{message}')
        if self.outline_list.currentRow() == row:
            self._render_section(row)
        self._refresh_generate_btn(self.outline_list.currentRow())

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



# 把关系图画成图片嵌进正文，并整理节点释义作为附注追加到末尾
def _append_legend(markdown: str, bundle: dict[str, Any]) -> str:
    visualization = bundle.get('visualization')
    if not isinstance(visualization, dict):
        return markdown

    nodes = [n for n in (visualization.get('nodes') or []) if isinstance(n, dict)]
    if not nodes:
        return markdown

    lines = ['', '---', '']

    # 正文已用占位符引用关系图就地渲染，就不在末尾重复贴；旧产物没有占位符时兜底追加。
    # 现在后端还没给 visualization 加 id、正文也不会写占位符，所以这里总是走兜底分支。
    graph_id = str(visualization.get('id') or '')
    referenced = bool(graph_id) and f'(media:{graph_id})' in markdown
    if not referenced:
        graph = visualization_to_image(visualization)
        if graph is not None and not graph.isNull():
            title = str(visualization.get('title') or '知识点关系图')
            lines.extend([
                '### 知识点关系图',
                '',
                # 图片旁的链接即「按钮」，点击后用 anchorClicked 在右侧展开交互图
                f'![{title}]({image_to_data_url(graph)})　[查看详细图](graph://show)',
                '',
            ])

    # 图片里只有节点名，这里再补每个节点的释义
    lines.append('### 图中概念释义')
    lines.append('')
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