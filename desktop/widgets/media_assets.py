# 素材绘制：把 diagram 规格画成图片，并提供图片转 data URL 的工具
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen

# 画布白底，与正文背景一致
BG_COLOR = '#ffffff'

# 线条与文字配色，适配白底
AXIS_COLOR = '#8a8a8a'
TEXT_COLOR = '#24292f'
LABEL_COLOR = '#57606a'
LINE_COLOR = '#0969da'
TITLE_COLOR = '#24292f'


# 把 QImage 编码成 PNG 的 data URL，便于内嵌进 HTML
def image_to_data_url(image: QImage) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    encoded = bytes(buffer.data().toBase64()).decode('ascii')
    buffer.close()
    return f'data:image/png;base64,{encoded}'


# 把 diagram 规格画成图片，失败返回 None
def diagram_to_image(diagram: dict[str, Any], *, width: int, height: int) -> QImage | None:
    if not isinstance(diagram, dict):
        return None

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(BG_COLOR))          # 白底

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        axis = diagram.get('axis') or {}
        plot = _plot_rect(width, height)
        _draw_frame(painter, diagram, plot)
        for series in (diagram.get('series') or []):
            _draw_series(painter, series, axis, plot)
        _draw_marks(painter, diagram.get('marks') or [], axis, plot)
    finally:
        painter.end()

    return image


# 计算绘图区，四周留出标题与轴标签空间
def _plot_rect(width: int, height: int) -> QRectF:
    return QRectF(70.0, 46.0, max(width - 110.0, 60.0), max(height - 92.0, 60.0))


# 画标题、边框与轴标签
def _draw_frame(painter: QPainter, diagram: dict[str, Any], plot: QRectF) -> None:
    title = str(diagram.get('title') or '')
    if title:
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor(TITLE_COLOR)))
        painter.drawText(
            QRectF(plot.left(), 10.0, plot.width(), 28.0),
            Qt.AlignmentFlag.AlignCenter,
            title
        )

    painter.setPen(QPen(QColor(AXIS_COLOR), 1.2))
    painter.drawRect(plot)

    axis = diagram.get('axis') or {}
    font = QFont()
    font.setPointSize(8)
    painter.setFont(font)
    painter.setPen(QPen(QColor(LABEL_COLOR)))

    x_label = str(axis.get('x_label') or '')
    if x_label:
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 26.0, plot.width(), 20.0),
            Qt.AlignmentFlag.AlignCenter,
            x_label
        )

    y_label = str(axis.get('y_label') or '')
    if y_label:
        painter.save()
        painter.translate(14.0, plot.center().y())
        painter.rotate(-90.0)
        painter.drawText(
            QRectF(-plot.height() / 2.0, -10.0, plot.height(), 20.0),
            Qt.AlignmentFlag.AlignCenter,
            y_label
        )
        painter.restore()


# 把数值坐标映射到绘图区像素位置
def _to_point(x: float, y: float, axis: dict[str, Any], plot: QRectF) -> tuple[float, float]:
    x_min = float(axis.get('x_min', 0.0))
    x_max = float(axis.get('x_max', 1.0))
    y_min = float(axis.get('y_min', 0.0))
    y_max = float(axis.get('y_max', 1.0))

    span_x = (x_max - x_min) or 1.0
    span_y = (y_max - y_min) or 1.0

    px = plot.left() + (x - x_min) / span_x * plot.width()
    # 屏幕 y 轴向下，数据 y 轴向上，需要翻转
    py = plot.bottom() - (y - y_min) / span_y * plot.height()
    return px, py


# 画一条折线及其数据点
def _draw_series(
    painter: QPainter,
    series: dict[str, Any],
    axis: dict[str, Any],
    plot: QRectF
) -> None:
    if not isinstance(series, dict):
        return

    points = [p for p in (series.get('points') or []) if isinstance(p, dict)]
    if len(points) < 2:
        return

    style = str(series.get('style') or 'solid')
    pen_style = {
        'solid': Qt.PenStyle.SolidLine,
        'dashed': Qt.PenStyle.DashLine,
        'dotted': Qt.PenStyle.DotLine,
    }.get(style, Qt.PenStyle.SolidLine)

    painter.setPen(QPen(QColor(LINE_COLOR), 2.0, pen_style))
    coords = [
        _to_point(float(p.get('x', 0.0)), float(p.get('y', 0.0)), axis, plot)
        for p in points
    ]

    for index in range(len(coords) - 1):
        painter.drawLine(
            int(coords[index][0]), int(coords[index][1]),
            int(coords[index + 1][0]), int(coords[index + 1][1])
        )

    painter.setBrush(QColor(LINE_COLOR))
    for px, py in coords:
        painter.drawEllipse(QRectF(px - 3.0, py - 3.0, 6.0, 6.0))


# 画数据点上方的文字标注
def _draw_marks(
    painter: QPainter,
    marks: list[dict[str, Any]],
    axis: dict[str, Any],
    plot: QRectF
) -> None:
    font = QFont()
    font.setPointSize(8)
    painter.setFont(font)
    painter.setPen(QPen(QColor(TEXT_COLOR)))

    for mark in marks:
        if not isinstance(mark, dict):
            continue

        text = str(mark.get('text') or '')
        if not text:
            continue

        px, py = _to_point(
            float(mark.get('x', 0.0)),
            float(mark.get('y', 0.0)),
            axis,
            plot
        )

        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text)
        # 标注水平居中，贴边时向内收
        x = min(max(px - width / 2.0, plot.left() + 2.0), plot.right() - width - 2.0)
        y = py - 10.0
        # 超出上边界时改画到点下方
        if y < plot.top() + metrics.height():
            y = py + metrics.height() + 4.0
        painter.drawText(int(x), int(y), text)