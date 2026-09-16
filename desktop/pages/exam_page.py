# 课后测验页：生成测验、作答、批阅并查看报告
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.api.client import LearningClient
from desktop.pages.report_dialog import show_report
from desktop.reporting import report_to_markdown
from desktop.workers import run_async
from desktop.widgets.markdown_renderer import render_markdown
from desktop.widgets.quiz_panel import QuizPanel

# 正文底色
CANVAS_COLOR = '#ffffff'

# 报告区样式：白底黑字
_STYLE_SHEET = """
body { background-color: #ffffff; color: #24292f; }
p { margin: 6px 0; }
h1, h2, h3 { color: #24292f; }
pre { background-color: #f6f8fa; padding: 8px; }
code { background-color: #f6f8fa; color: #953800; }
a { color: #0969da; }
table { border-collapse: collapse; margin: 8px 0; }
th, td { border: 1px solid #d0d7de; padding: 4px 8px; }
th { background-color: #f6f8fa; }
blockquote { border-left: 3px solid #d0d7de; margin: 8px 0; padding-left: 12px; color: #57606a; }
hr { border: none; border-top: 1px solid #d0d7de; }
"""


# 课后测验页
class ExamPage(QWidget):
    # 返回学习页
    back_requested = pyqtSignal()

    # 客户端由主窗口注入
    def __init__(self, client: LearningClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._session: dict[str, Any] = {}
        self._session_id: str = ''
        self._build_ui()

    # 搭控件树
    def _build_ui(self) -> None:
        self.topic_label = QLabel('课后测验')
        self.topic_label.setStyleSheet('font-size: 16px; font-weight: bold; color: #24292f;')

        self.phase_label = QLabel('')
        self.phase_label.setStyleSheet('color: #57606a;')

        self.back_btn = QPushButton('重新学习')
        self.back_btn.setToolTip('返回学习页复习已生成的资料（学习阶段已结束，不能再生成）')
        self.back_btn.clicked.connect(self.back_requested.emit)

        self.generate_btn = QPushButton('生成课后测验')
        self.generate_btn.clicked.connect(self._on_generate)

        self.summary_btn = QPushButton('生成学习报告')
        self.summary_btn.setEnabled(False)
        self.summary_btn.clicked.connect(self._on_summarize)

        self.report_btn = QPushButton('查看学习报告')
        self.report_btn.clicked.connect(self._on_view_report)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        self.status = QLabel('')
        self.status.setStyleSheet('color: #57606a;')
        self.status.setWordWrap(True)

        # 左：作答区；右：报告区
        self.quiz_panel = QuizPanel(
            self._client.grade_exercises,
            empty_hint='还没有题目。点上方「生成课后测验」开始。',
            submit_text='提交并批阅'
        )
        self.quiz_panel.graded.connect(self._on_graded)

        self.report_box = QTextBrowser()
        self.report_box.setOpenExternalLinks(True)
        self.report_box.setPlaceholderText('批阅后这里显示学习报告')
        self.report_box.setStyleSheet(
            f'QTextBrowser {{ background-color: {CANVAS_COLOR}; color: #24292f; border: none; }}'
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.quiz_panel)
        splitter.addWidget(self.report_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        header = QHBoxLayout()
        header.addWidget(self.topic_label)
        header.addWidget(self.phase_label, 1)
        header.addWidget(self.generate_btn)
        header.addWidget(self.summary_btn)
        header.addWidget(self.report_btn)
        header.addWidget(self.back_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self.progress)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status)

    # 用会话快照填充页面
    def load_session(self, session: dict[str, Any]) -> None:
        self._session = session
        self._session_id = session.get('session_id', '')

        self.topic_label.setText(f'课后测验 · {session.get("topic") or "未命名主题"}')
        self.phase_label.setText(f'阶段：{session.get("phase", "?")}')

        # 切到本页时重置，题目需重新生成（后端没有读题库的接口）
        self.quiz_panel.load_exercises(self._session_id, [])
        self.report_box.clear()
        self.summary_btn.setEnabled(False)
        self.status.setText('点「生成课后测验」，系统会依据全章知识点出题。')

    # 生成课后测验
    def _on_generate(self) -> None:
        if not self._session_id:
            QMessageBox.warning(self, '会话缺失', '请先打开一个学习会话。')
            return

        self.status.setText('正在生成课后测验…（可能需一到数分钟）')
        run_async(
            self._client.generate_exercises,
            self._session_id,
            on_start=self._lock,
            on_ok=self._on_generated,
            on_error=self._on_error,
            on_finish=self._unlock,
        )

    # 测验生成完成：把题目交给作答面板
    def _on_generated(self, data: Any) -> None:
        if not isinstance(data, dict):
            self.status.setText('后端返回了未预期的结构。')
            return

        exercise_set = data.get('exercise_set') or {}
        exercises = [
            item for item in (exercise_set.get('exercises') or [])
            if isinstance(item, dict)
        ]
        status = str(data.get('status') or '')
        warn = '（校验未通过，已降级交付）' if status == 'accepted_with_warning' else ''

        self.quiz_panel.load_exercises(self._session_id, exercises)
        self.status.setText(f'已生成 {len(exercises)} 道题{warn}。')
        self.summary_btn.setEnabled(True)

    # 批阅完成：报告按钮可用
    def _on_graded(self, data: Any) -> None:
        correct = int(data.get('correct_count') or 0)
        total = int(data.get('total') or 0)
        self.status.setText(f'批阅完成，得分 {correct} / {total}。可点「生成学习报告」归档。')

    # 生成学习报告
    def _on_summarize(self) -> None:
        if not self._session_id:
            return

        self.status.setText('正在汇总学习报告…')
        run_async(
            self._client.summarize,
            self._session_id,
            on_start=self._lock,
            on_ok=self._on_summarized,
            on_error=self._on_error,
            on_finish=self._unlock,
        )

    # 报告生成完成
    def _on_summarized(self, data: Any) -> None:
        if not isinstance(data, dict):
            self.status.setText('后端返回了未预期的结构。')
            return

        html = render_markdown(report_to_markdown(data))
        document = self.report_box.document()
        document.setDefaultStyleSheet(_STYLE_SHEET)
        document.setHtml(html)
        self.status.setText('学习报告已生成。')

    # 查看已归档的学习报告
    def _on_view_report(self) -> None:
        if not self._session_id:
            return
        show_report(self, self._client, self._session_id)

    # 执行期间锁住按钮
    def _lock(self) -> None:
        self.generate_btn.setEnabled(False)
        self.summary_btn.setEnabled(False)
        self.back_btn.setEnabled(False)
        self.progress.show()

    # 执行结束恢复
    def _unlock(self) -> None:
        self.generate_btn.setEnabled(True)
        self.back_btn.setEnabled(True)
        self.progress.hide()
        # 有题目才允许出报告
        self.summary_btn.setEnabled(self.quiz_panel.has_exercises())

    # 失败处理
    def _on_error(self, code: str, message: str) -> None:
        self.status.setText(f'失败：{message}')
        if code == 'phase_guard_violation':
            QMessageBox.warning(
                self, '当前阶段不允许该操作',
                f'{message}\n\n课后测验需要在「已学完」阶段进行。'
            )
            return
        if code == 'client_offline':
            QMessageBox.critical(self, '后端未启动', f'{message}\n\n请先启动后端服务。')
            return
        QMessageBox.critical(self, '操作失败', f'[{code}] {message}')