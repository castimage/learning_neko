# 全局主题：现代靛蓝/石板灰配色的 QSS，以及用代码绘制的应用图标
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

# 现代配色令牌：靛蓝主色 + 中性石板灰
PRIMARY = '#6366f1'          # 主色（indigo-500）
PRIMARY_DARK = '#4338ca'     # 深主色（indigo-700）
PRIMARY_LIGHT = '#818cf8'    # 浅主色（indigo-400）
ACCENT_SOFT = '#eef2ff'      # 主色柔和底（indigo-50）
INK = '#111827'
TEXT = '#1f2937'
TEXT_MUTED = '#6b7280'
BORDER = '#e5e7eb'
BORDER_STRONG = '#d1d5db'
BG = '#f8fafc'
CARD = '#ffffff'
HOVER = '#f8fafc'
SUBTLE = '#f1f5f9'
PASS = '#10b981'
WARN = '#f59e0b'
DANGER = '#ef4444'

# 全局样式表：统一字体、圆角、控件外观、滚动条与标签页
APP_STYLE = f"""
QWidget {{
    font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BG}; }}
QMenuBar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 5px 12px; background: transparent; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {SUBTLE}; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {PRIMARY_DARK}; }}
QToolBar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; spacing: 6px; padding: 5px; }}
QStatusBar {{ background: {CARD}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED}; }}

QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: {HOVER}; border-color: {PRIMARY_LIGHT}; }}
QPushButton:pressed {{ background: {SUBTLE}; }}
QPushButton:disabled {{ color: #9ca3af; background: {BG}; border-color: {BORDER}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {PRIMARY_LIGHT}; color: {PRIMARY_DARK}; }}
QPushButton:focus {{ outline: none; border-color: {PRIMARY}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: #c7d2fe;
    selection-color: {INK};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {PRIMARY}; }}

QListWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 5px; }}
QListWidget::item {{ padding: 7px 10px; border-radius: 8px; }}
QListWidget::item:hover {{ background: {HOVER}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {PRIMARY_DARK}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {CARD}; top: -1px; }}
QTabBar::tab {{
    background: transparent;
    padding: 7px 16px;
    margin-right: 3px;
    border: 1px solid transparent;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    color: {TEXT_MUTED};
}}
QTabBar::tab:selected {{
    background: {CARD};
    color: {PRIMARY_DARK};
    border: 1px solid {BORDER};
    border-bottom-color: {CARD};
}}
QTabBar::tab:hover {{ color: {PRIMARY}; }}

QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: {CARD};
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 5px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 5px; min-height: 26px; }}
QScrollBar::handle:vertical:hover {{ background: #9ca3af; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 5px; min-width: 26px; }}
QScrollBar::handle:horizontal:hover {{ background: #9ca3af; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QScrollArea {{ background: {CARD}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {CARD}; }}
QAbstractScrollArea::corner {{ background: {CARD}; }}

QTableWidget, QTableView {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {PRIMARY_DARK};
    alternate-background-color: {BG};
}}
QTableWidget::item, QTableView::item {{ padding: 5px 8px; }}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {ACCENT_SOFT};
    color: {PRIMARY_DARK};
}}
QHeaderView {{ background: {SUBTLE}; }}
QHeaderView::section {{
    background: {SUBTLE};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
    padding: 6px 8px;
}}
QTableCornerButton::section {{
    background: {SUBTLE};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
}}

QRadioButton, QCheckBox {{ spacing: 7px; padding: 2px; }}
QRadioButton::indicator, QCheckBox::indicator {{ width: 16px; height: 16px; }}
QRadioButton::indicator {{
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    background: {CARD};
}}
QRadioButton::indicator:hover {{ border-color: {PRIMARY_LIGHT}; }}
QRadioButton::indicator:checked {{
    border: 5px solid {PRIMARY};
    background: {CARD};
}}
QCheckBox::indicator {{
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {CARD};
}}
QCheckBox::indicator:hover {{ border-color: {PRIMARY_LIGHT}; }}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}

QSplitter::handle {{ background: #eef2f7; }}
QSplitter::handle:horizontal {{ width: 5px; }}
QSplitter::handle:vertical {{ height: 5px; }}
QSplitter::handle:hover {{ background: {PRIMARY_LIGHT}; }}

QToolTip {{
    background: {INK};
    color: {CARD};
    border: none;
    padding: 5px 8px;
    border-radius: 6px;
}}
"""

_ICON_CACHE: QIcon | None = None


# 画一个尺寸的猫脸图标：靛蓝渐变底 + 白猫头，用于窗口与任务栏
def _draw_icon(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = float(size)

    # 圆角渐变底
    gradient = QLinearGradient(0.0, 0.0, scale, scale)
    gradient.setColorAt(0.0, QColor('#818cf8'))
    gradient.setColorAt(1.0, QColor('#4338ca'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(
        QRectF(scale * 0.04, scale * 0.04, scale * 0.92, scale * 0.92),
        scale * 0.24,
        scale * 0.24
    )

    white = QBrush(QColor('#ffffff'))
    ink = QBrush(QColor('#312e81'))

    # 两只耳朵
    painter.setBrush(white)
    painter.drawPolygon(QPolygonF([
        QPointF(scale * 0.24, scale * 0.46),
        QPointF(scale * 0.30, scale * 0.16),
        QPointF(scale * 0.47, scale * 0.32),
    ]))
    painter.drawPolygon(QPolygonF([
        QPointF(scale * 0.76, scale * 0.46),
        QPointF(scale * 0.70, scale * 0.16),
        QPointF(scale * 0.53, scale * 0.32),
    ]))

    # 脑袋
    painter.setBrush(white)
    painter.drawEllipse(QRectF(scale * 0.20, scale * 0.26, scale * 0.60, scale * 0.52))

    # 眼睛
    painter.setBrush(ink)
    painter.drawEllipse(QRectF(scale * 0.35, scale * 0.44, scale * 0.08, scale * 0.10))
    painter.drawEllipse(QRectF(scale * 0.57, scale * 0.44, scale * 0.08, scale * 0.10))

    # 鼻子
    painter.setBrush(QBrush(QColor('#fb7185')))
    painter.drawPolygon(QPolygonF([
        QPointF(scale * 0.47, scale * 0.61),
        QPointF(scale * 0.53, scale * 0.61),
        QPointF(scale * 0.50, scale * 0.66),
    ]))

    # 胡须
    pen = QPen(QColor('#312e81'))
    pen.setWidthF(max(1.0, scale * 0.018))
    painter.setPen(pen)
    painter.drawLine(QPointF(scale * 0.16, scale * 0.58), QPointF(scale * 0.32, scale * 0.60))
    painter.drawLine(QPointF(scale * 0.84, scale * 0.58), QPointF(scale * 0.68, scale * 0.60))

    painter.end()
    return pixmap


# 生成多尺寸应用图标，缓存复用
def build_app_icon() -> QIcon:
    global _ICON_CACHE
    if _ICON_CACHE is None:
        icon = QIcon()
        for size in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(_draw_icon(size))
        _ICON_CACHE = icon
    return _ICON_CACHE


# 把主题样式与应用图标装到 QApplication 上
def apply_theme(app: object) -> None:
    app.setStyleSheet(APP_STYLE)
    app.setWindowIcon(build_app_icon())
