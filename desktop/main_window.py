# 主窗口：只负责装页面、切页面与页面间协调，不写业务逻辑
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QWidget

from desktop.api.client import LearningClient
from desktop.pages.start_page import StartPage
from desktop.pages.study_page import StudyPage


# 主窗口
class MainWindow(QMainWindow):
    # 客户端由入口注入
    def __init__(self, client: LearningClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle('learning-neko')
        self.resize(1100, 760)

        # ① 建页面并放进堆栈
        self._stack = QStackedWidget()
        self._start_page = StartPage(client)
        self._study_page = StudyPage(client)
        self._stack.addWidget(self._start_page)
        self._stack.addWidget(self._study_page)
        self.setCentralWidget(self._stack)

        # ② 接线：页面之间不互相引用，一律经主窗口转发
        self._start_page.session_started.connect(self._on_session_started)

        # ③ 启动落在起始页
        self._stack.setCurrentWidget(self._start_page)

    # 会话创建成功：把快照交给学习页并切过去
    def _on_session_started(self, session: Any) -> None:
        if not isinstance(session, dict):
            return
        self._study_page.load_session(session)
        self._stack.setCurrentWidget(self._study_page)