# PyQt 桌面端启动入口
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from desktop.api.client import LearningClient
from desktop.main_window import MainWindow

# 后端地址，后续可改为从配置文件读取
BACKEND_URL = 'http://127.0.0.1:8000'


# 建应用、建窗口、进事件循环
def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName('learning-neko')
    app.setOrganizationName('jiangxingzhao')

    client = LearningClient(BACKEND_URL)
    window = MainWindow(client)
    window.show()

    code = app.exec()
    client.close()
    return code


if __name__ == '__main__':
    sys.exit(main())