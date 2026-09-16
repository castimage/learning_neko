# 学习报告查看：拉取已归档报告，用对话框展示
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QDialog,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.api.client import LearningClient
from desktop.reporting import report_to_markdown
from desktop.widgets.markdown_renderer import render_markdown
from desktop.workers import run_async

# 报告区样式：白底黑字
REPORT_STYLE = """
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


# 拉取并展示学习报告，未归档时给出提示
def show_report(parent: QWidget, client: LearningClient, session_id: str) -> None:
    run_async(
        client.report,
        session_id,
        on_ok=lambda data: _present(parent, data),
        on_error=lambda code, message: _failed(parent, code, message),
    )


# 展示报告对话框
def _present(parent: QWidget, data: Any) -> None:
    if not isinstance(data, dict):
        QMessageBox.warning(parent, '读取报告失败', '后端返回了未预期的结构。')
        return

    dialog = QDialog(parent)
    dialog.setWindowTitle('学习报告')
    dialog.resize(760, 600)

    box = QTextBrowser()
    box.setOpenExternalLinks(True)
    document = box.document()
    document.setDefaultStyleSheet(REPORT_STYLE)
    document.setHtml(render_markdown(report_to_markdown(data)))

    close_btn = QPushButton('关闭')
    close_btn.clicked.connect(dialog.accept)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    layout.addWidget(box, 1)
    layout.addWidget(close_btn)
    dialog.exec()


# 读取失败：区分未归档与后端离线
def _failed(parent: QWidget, code: str, message: str) -> None:
    if code == 'artifact_not_found':
        QMessageBox.information(
            parent, '尚无学习报告',
            f'{message}\n\n请先在课后测验页点「生成学习报告」。'
        )
        return
    if code == 'client_offline':
        QMessageBox.critical(parent, '后端未启动', f'{message}\n\n请先启动后端服务。')
        return
    QMessageBox.critical(parent, '读取报告失败', f'[{code}] {message}')
