# 作答面板：渲染题目、收集答案、接收判定，例题与课后测验共用
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop.workers import run_async


# 单道题的作答控件
class QuestionCard(QFrame):
    # 作答内容变化时抛出，供外层刷新提交按钮
    changed = pyqtSignal()

    # 选择题用单选按钮组，填空题用输入框
    def __init__(self, exercise: dict[str, Any], index: int) -> None:
        super().__init__()
        self.setObjectName('card')
        self.exercise = exercise
        self.q_id = str(exercise.get('q_id') or f'q{index}')
        self._options = [str(o) for o in (exercise.get('options') or [])]
        self._group: QButtonGroup | None = None
        self._edit: QLineEdit | None = None
        self._verdict: QLabel | None = None
        self._analysis: QLabel | None = None
        self._build_ui(index)
        self.setStyleSheet(
            'QFrame#card { background-color: #ffffff; border: 1px solid #d0d7de;'
            ' border-radius: 6px; }'
        )

    # 搭单题控件
    def _build_ui(self, index: int) -> None:
        question = str(self.exercise.get('question') or '')
        difficulty = str(self.exercise.get('difficulty') or '')
        knowledge = str(self.exercise.get('knowledge_point') or '')

        # 题号、难度与知识点
        head = QLabel(f'第 {index} 题　{difficulty}　{knowledge}')
        head.setStyleSheet('color: #57606a; font-size: 12px;')

        text = QLabel(question)
        text.setWordWrap(True)
        text.setStyleSheet('color: #24292f; font-weight: bold;')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addWidget(head)
        layout.addWidget(text)

        if self._options:
            self._group = QButtonGroup(self)
            for position, option in enumerate(self._options):
                radio = QRadioButton(option)
                radio.setStyleSheet('color: #24292f;')
                self._group.addButton(radio, position)
                layout.addWidget(radio)
            self._group.buttonToggled.connect(lambda *_: self.changed.emit())
        else:
            self._edit = QLineEdit()
            self._edit.setPlaceholderText('在此填写答案')
            self._edit.textChanged.connect(lambda *_: self.changed.emit())
            layout.addWidget(self._edit)

        # 判定结果与解析，未提交时不显示
        self._verdict = QLabel()
        self._verdict.setWordWrap(True)
        self._verdict.hide()
        self._analysis = QLabel()
        self._analysis.setWordWrap(True)
        self._analysis.setStyleSheet('color: #57606a;')
        self._analysis.hide()
        layout.addWidget(self._verdict)
        layout.addWidget(self._analysis)

    # 取当前作答内容，未作答返回空串
    def answer(self) -> str:
        if self._group is not None:
            button = self._group.checkedButton()
            return button.text() if button is not None else ''
        if self._edit is not None:
            return self._edit.text().strip()
        return ''

    # 是否已作答
    def answered(self) -> bool:
        return bool(self.answer())

    # 锁住作答控件
    def lock(self) -> None:
        if self._group is not None:
            for button in self._group.buttons():
                button.setEnabled(False)
        if self._edit is not None:
            self._edit.setReadOnly(True)

    # 解锁并清空判定，供重新作答
    def unlock(self) -> None:
        if self._group is not None:
            for button in self._group.buttons():
                button.setEnabled(True)
        if self._edit is not None:
            self._edit.setReadOnly(False)
        if self._verdict is not None:
            self._verdict.hide()
        if self._analysis is not None:
            self._analysis.hide()

    # 显示判定结果与解析
    def show_verdict(self, correct: bool, reason: str, advice: str = '') -> None:
        if self._verdict is None or self._analysis is None:
            return

        mark = '✓ 正确' if correct else '✗ 错误'
        self._verdict.setText(f'{mark}　{reason}' if reason else mark)
        self._verdict.setStyleSheet(
            'color: #1a7f37; font-weight: bold;' if correct
            else 'color: #cf222e; font-weight: bold;'
        )
        self._verdict.show()

        parts: list[str] = []
        answer = str(self.exercise.get('answer') or '')
        if answer:
            parts.append(f'参考答案：{answer}')
        analysis = str(self.exercise.get('analysis') or '')
        if analysis:
            parts.append(f'解析：{analysis}')
        if advice:
            parts.append(f'建议：{advice}')
        if parts:
            self._analysis.setText('\n'.join(parts))
            self._analysis.show()


# 整卷作答面板
class QuizPanel(QWidget):
    # 批改完成对外抛出判定结果
    graded = pyqtSignal(object)

    # 由外面注入提交函数与文案
    def __init__(
        self,
        submit: Any,
        *,
        empty_hint: str,
        submit_text: str = '提交作答',
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._submit = submit
        self._empty_hint = empty_hint
        self._submit_text = submit_text
        self._session_id: str = ''
        self._cards: list[QuestionCard] = []
        self._busy = False
        # 是否已批改过，决定按钮是「提交」还是「重新作答」
        self._graded = False
        # 本节是否允许提交判定，只读模式下只展示题目
        self._gradeable = True
        self._build_ui()

    # 搭控件树
    def _build_ui(self) -> None:
        self.hint = QLabel('')
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet('color: #57606a;')

        # 题目区放进滚动容器，题目多时不会撑破窗口
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.cards_host)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        self.submit_btn = QPushButton(self._submit_text)
        self.submit_btn.setEnabled(False)
        self.submit_btn.clicked.connect(self._on_submit)

        self.score_label = QLabel('')
        self.score_label.setStyleSheet('font-weight: bold; color: #24292f;')

        bottom = QHBoxLayout()
        bottom.addWidget(self.score_label, 1)
        bottom.addWidget(self.submit_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.hint)
        layout.addWidget(self.progress)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(bottom)

    # 装载题集
    def load_exercises(self, session_id: str, exercises: list[dict[str, Any]]) -> None:
        self._session_id = session_id
        self._graded = False
        self._gradeable = True
        self._clear_cards()

        items = [e for e in exercises if isinstance(e, dict)]
        for index, item in enumerate(items, start=1):
            card = QuestionCard(item, index)
            card.changed.connect(self._sync_submit)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
            self._cards.append(card)

        self.score_label.setText('')
        self.submit_btn.setText(self._submit_text)

        if not self._cards:
            self.hint.setText(self._empty_hint)
            self.submit_btn.setEnabled(False)
            return

        self._sync_submit()

    # 清空已有题目，保留末尾的 stretch
    def _clear_cards(self) -> None:
        self._cards = []
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # 按作答情况刷新提示与提交按钮
    def _sync_submit(self) -> None:
        if not self._cards:
            self.submit_btn.setEnabled(False)
            return

        unanswered = sum(1 for c in self._cards if not c.answered())
        enabled = unanswered == 0 and not self._busy and self._gradeable
        self.submit_btn.setEnabled(enabled)

        if self._busy:
            return
        if not self._gradeable:
            self.hint.setText(f'共 {len(self._cards)} 题。本节需重新生成资料后才能提交判定。')
        elif unanswered:
            self.hint.setText(f'共 {len(self._cards)} 题，还有 {unanswered} 题未作答。')
        else:
            self.hint.setText(f'共 {len(self._cards)} 题，已全部作答，可以提交。')

    # 切换本节能否提交判定，只读时题目仍可作答自测
    def set_gradeable(self, gradeable: bool) -> None:
        self._gradeable = gradeable
        self._sync_submit()

    # 提交整卷作答，或重新作答
    def _on_submit(self) -> None:
        if self._busy or not self._cards:
            return

        # 已批改过说明这次是「重新作答」，只解锁不提交
        if self._graded:
            self._reset_for_retry()
            return

        if not self._gradeable:
            return

        answers = [{'q_id': c.q_id, 'answer': c.answer()} for c in self._cards]
        if any(not item['answer'] for item in answers):
            QMessageBox.information(self, '还有空题', '请先把所有题目作答完毕。')
            return

        self._set_busy(True)
        run_async(
            self._submit,
            self._session_id,
            answers,
            on_ok=self._on_graded,
            on_error=self._on_error,
            on_finish=lambda: self._set_busy(False),
        )

    # 重新作答：解锁全部题目并清空判定
    def _reset_for_retry(self) -> None:
        self._graded = False
        self.score_label.setText('')
        self.submit_btn.setText(self._submit_text)
        for card in self._cards:
            card.unlock()
        self._sync_submit()

    # 收到判定：逐题显示
    def _on_graded(self, data: Any) -> None:
        if not isinstance(data, dict):
            self.hint.setText('后端返回了未预期的结构。')
            return

        results = {
            str(item.get('q_id')): item
            for item in (data.get('results') or [])
            if isinstance(item, dict)
        }

        for card in self._cards:
            verdict = results.get(card.q_id)
            if verdict is None:
                continue
            card.lock()
            card.show_verdict(
                bool(verdict.get('correct')),
                str(verdict.get('reason') or ''),
                str(verdict.get('advice') or '')
            )

        correct = int(data.get('correct_count') or 0)
        total = int(data.get('total') or len(self._cards))
        self.score_label.setText(f'得分：{correct} / {total}')
        self.hint.setText('已批改，点「重新作答」可再练一遍。')
        self._graded = True
        self.submit_btn.setText('重新作答')
        self.submit_btn.setEnabled(True)
        self.graded.emit(data)

    # 提交失败
    def _on_error(self, code: str, message: str) -> None:
        self.hint.setText(f'提交失败：{message}')
        tips = {
            'phase_guard_violation': '当前学习阶段不允许该操作。',
            'artifact_not_found': '题目尚未生成。',
            'client_offline': '后端未启动。',
        }
        tip = tips.get(code, '')
        if tip:
            QMessageBox.warning(self, '提交失败', f'{message}\n\n{tip}')
            return
        QMessageBox.critical(self, '提交失败', f'[{code}] {message}')

    # 忙碌状态
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.submit_btn.setEnabled(False)
            return
        self._sync_submit()

    # 是否已装载题目
    def has_exercises(self) -> bool:
        return bool(self._cards)

    # 正在忙吗
    def is_busy(self) -> bool:
        return self._busy

    # 已批改过吗
    def is_graded(self) -> bool:
        return self._graded