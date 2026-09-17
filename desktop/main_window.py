# 主窗口：装页面、切页面、页面间协调，以及全局操作的唯一入口
from __future__ import annotations

from typing import Any

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QDialog, QMainWindow, QMessageBox, QStackedWidget

from desktop.api.client import LearningClient
from desktop.pages.history_dialog import HistoryDialog
from desktop.pages.start_page import StartPage
from desktop.pages.study_page import StudyPage
from desktop.workers import run_async
from desktop.pages.exam_page import ExamPage


# 主窗口
class MainWindow(QMainWindow):
    # 客户端由入口注入
    def __init__(self, client: LearningClient) -> None:
        super().__init__()
        self._client = client
        self.setWindowTitle('learning-neko')
        self.resize(1100, 760)

        # ① 建页面并放进堆栈
        self._stack = QStackedWidget()
        self._start_page = StartPage(client)
        self._study_page = StudyPage(client)
        self._exam_page = ExamPage(client)
        self._stack.addWidget(self._exam_page)
        self._stack.addWidget(self._start_page)
        self._stack.addWidget(self._study_page)
        self.setCentralWidget(self._stack)

        # ② 全局动作：任何页面都可用，不隶属于某个页面
        self._build_actions()
        self._build_menus()

        # ③ 接线：页面之间不互相引用，一律经主窗口转发
        self._start_page.session_started.connect(self._on_session_ready)
        self._study_page.session_completed.connect(self._on_session_completed)
        self._study_page.exam_requested.connect(self._on_session_completed)
        self._exam_page.back_requested.connect(self._back_to_study)

        # ④ 启动落在起始页
        self._stack.setCurrentWidget(self._start_page)

    # 建全局动作，菜单与工具栏共用同一批 QAction
    def _build_actions(self) -> None:
        self.action_new = QAction('新建学习(&N)', self)
        self.action_new.setShortcut(QKeySequence.StandardKey.New)
        self.action_new.setStatusTip('回到起始页，开始一次新的学习')
        self.action_new.triggered.connect(self._on_new_session)

        self.action_open = QAction('打开历史会话(&O)…', self)
        self.action_open.setShortcut(QKeySequence('Ctrl+O'))
        self.action_open.setStatusTip('从服务端列出已有会话并继续学习')
        self.action_open.triggered.connect(self._on_open_history)

        self.action_refresh = QAction('重新读取当前会话(&R)', self)
        self.action_refresh.setShortcut(QKeySequence.StandardKey.Refresh)
        self.action_refresh.setStatusTip('以服务端数据为准刷新当前页面')
        self.action_refresh.triggered.connect(self._on_refresh_current)

        self.action_quit = QAction('退出(&Q)', self)
        self.action_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_quit.triggered.connect(self.close)

    # 组菜单栏与工具栏
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu('文件(&F)')
        file_menu.addAction(self.action_new)
        file_menu.addAction(self.action_open)
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)

        view_menu = self.menuBar().addMenu('视图(&V)')
        view_menu.addAction(self.action_refresh)

        toolbar = self.addToolBar('常用')
        toolbar.setMovable(False)
        toolbar.addAction(self.action_new)
        toolbar.addAction(self.action_open)
        toolbar.addSeparator()
        toolbar.addAction(self.action_refresh)

        self.statusBar().showMessage('就绪')

    # 新建：回到起始页并清空状态
    def _on_new_session(self) -> None:
        self._start_page.reset()
        self._stack.setCurrentWidget(self._start_page)
        self.statusBar().showMessage('已回到起始页，可以开始新的学习')

    # 打开历史会话：任何页面都能触发
    def _on_open_history(self) -> None:
        dialog = HistoryDialog(self._client, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        session_id = dialog.selected_session_id
        if not session_id:
            return

        # 列表只带摘要，这里拉完整快照，保证以服务端数据为准
        self.statusBar().showMessage('正在打开会话…')
        self.action_open.setEnabled(False)
        run_async(
            self._client.snapshot,
            session_id,
            on_ok=self._on_session_ready,
            on_error=self._on_open_error,
            on_finish=lambda: self.action_open.setEnabled(True),
        )

    # 会话就绪：无论是新建还是打开，都走这里
    def _on_session_ready(self, session: Any) -> None:
        if not isinstance(session, dict):
            self.statusBar().showMessage('后端返回了未预期的结构')
            return
        self._study_page.load_session(session)
        self._stack.setCurrentWidget(self._study_page)
        self.statusBar().showMessage(
            f'已打开：{session.get("topic", "")}｜会话 {str(session.get("session_id", ""))[:8]}'
        )

    # 打开失败：会话被清理或后端未起
    def _on_open_error(self, code: str, message: str) -> None:
        self.statusBar().showMessage(f'打开失败：{message}')
        if code == 'session_not_found':
            QMessageBox.warning(
                self, '会话不存在',
                f'{message}\n\n该会话可能已被清理，请重新打开列表选择。'
            )
            return
        if code == 'client_offline':
            QMessageBox.critical(self, '后端未启动', f'{message}\n\n请先启动后端服务。')
            return
        QMessageBox.critical(self, '打开会话失败', f'[{code}] {message}')

    # 重新读取当前会话，用服务端数据覆盖本地视图
    def _on_refresh_current(self) -> None:
        session_id = self._study_page.current_session_id()
        if not session_id:
            self.statusBar().showMessage('当前没有打开的会话')
            return

        self.statusBar().showMessage('正在重新读取会话…')
        run_async(
            self._client.snapshot,
            session_id,
            on_ok=self._on_session_ready,
            on_error=self._on_open_error,
        )

    # 学完之后切到课后测验页
    def _on_session_completed(self, session: Any) -> None:
        if not isinstance(session, dict):
            return
        self._exam_page.load_session(session)
        self._stack.setCurrentWidget(self._exam_page)
        self.statusBar().showMessage('已进入课后测验')

    # 从课后测验返回学习页
    def _back_to_study(self) -> None:
        self._stack.setCurrentWidget(self._study_page)
        self.statusBar().showMessage('已返回学习页')