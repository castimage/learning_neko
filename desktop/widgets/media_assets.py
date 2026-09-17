# 素材绘制：把 diagram 规格画成图片，并提供图片转 data URL 的工具
from __future__ import annotations

import math
from typing import Any

from PyQt6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPolygonF,
)

from desktop.widgets.graph_layout import (
    NODE_HEIGHT as GRAPH_NODE_HEIGHT,
    NODE_WIDTH as GRAPH_NODE_WIDTH,
    assign_levels as _graph_levels,
    content_bounds as _graph_bounds,
    edge_anchors as _edge_anchors,
    edge_path as _edge_path,
    layout_nodes as _graph_layout,
)

# 画布白底，与正文背景一致
BG_COLOR = '#ffffff'

# 线条与文字配色，适配白底
AXIS_COLOR = '#8a8a8a'
TEXT_COLOR = '#24292f'
LABEL_COLOR = '#57606a'
LINE_COLOR = '#0969da'
TITLE_COLOR = '#24292f'
# 多条曲线依次取用的颜色，保证同一张图里能区分
SERIES_COLORS = (LINE_COLOR, '#cf222e', '#1a7f37', '#9a6700', '#8250df', '#0550ae')


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
        paint_diagram(painter, diagram, width, height)
    finally:
        painter.end()

    return image


# 在给定画布上绘制 diagram，静态出图与交互视图共用
def paint_diagram(painter: QPainter, diagram: dict[str, Any], width: int, height: int) -> None:
    if not isinstance(diagram, dict):
        return

    axis = diagram.get('axis') or {}
    plot = _plot_rect(width, height)
    _draw_frame(painter, diagram, plot)
    _draw_ticks(painter, axis, plot)

    legend: list[tuple[str, str]] = []
    series_list = [s for s in (diagram.get('series') or []) if isinstance(s, dict)]
    for index, series in enumerate(series_list):
        color = SERIES_COLORS[index % len(SERIES_COLORS)]
        _draw_series(painter, series, axis, plot, color)
        label = str(series.get('label') or '').strip()
        if label:
            legend.append((color, label))

    _draw_marks(painter, diagram.get('marks') or [], axis, plot)
    _draw_legend(painter, legend, plot)


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
    plot: QRectF,
    color: str
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

    painter.setPen(QPen(QColor(color), 2.0, pen_style))
    coords = [
        _to_point(float(p.get('x', 0.0)), float(p.get('y', 0.0)), axis, plot)
        for p in points
    ]

    for index in range(len(coords) - 1):
        painter.drawLine(
            int(coords[index][0]), int(coords[index][1]),
            int(coords[index + 1][0]), int(coords[index + 1][1])
        )

    painter.setBrush(QColor(color))
    for px, py in coords:
        painter.drawEllipse(QRectF(px - 3.0, py - 3.0, 6.0, 6.0))


# 在坐标轴上标出几档刻度数值
def _draw_ticks(painter: QPainter, axis: dict[str, Any], plot: QRectF) -> None:
    font = QFont()
    font.setPointSize(7)
    painter.setFont(font)
    painter.setPen(QPen(QColor(LABEL_COLOR)))
    metrics = painter.fontMetrics()

    x_min = float(axis.get('x_min', 0.0))
    x_max = float(axis.get('x_max', 1.0))
    y_min = float(axis.get('y_min', 0.0))
    y_max = float(axis.get('y_max', 1.0))

    for step in range(5):
        ratio = step / 4.0

        px, _ = _to_point(x_min + (x_max - x_min) * ratio, y_min, axis, plot)
        text = _format_tick(x_min + (x_max - x_min) * ratio)
        painter.drawText(
            QPointF(px - metrics.horizontalAdvance(text) / 2.0, plot.bottom() + metrics.height() + 1.0),
            text
        )

        _, py = _to_point(x_min, y_min + (y_max - y_min) * ratio, axis, plot)
        text = _format_tick(y_min + (y_max - y_min) * ratio)
        painter.drawText(
            QPointF(plot.left() - metrics.horizontalAdvance(text) - 4.0, py + metrics.ascent() / 2.0),
            text
        )


# 刻度数值去掉多余小数
def _format_tick(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f'{value:.2f}'.rstrip('0').rstrip('.')


# 图例：每条曲线一个色块加名称，压在绘图区右上角
def _draw_legend(painter: QPainter, entries: list[tuple[str, str]], plot: QRectF) -> None:
    if not entries:
        return

    font = QFont()
    font.setPointSize(8)
    painter.setFont(font)
    metrics = painter.fontMetrics()

    line_h = metrics.height() + 3.0
    width = max(metrics.horizontalAdvance(label) for _, label in entries) + 34.0
    height = line_h * len(entries) + 6.0
    left = plot.right() - width - 8.0
    top = plot.top() + 6.0

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
    painter.drawRoundedRect(QRectF(left, top, width, height), 4.0, 4.0)

    y = top + 3.0
    for color, label in entries:
        mid = y + metrics.height() / 2.0
        painter.setPen(QPen(QColor(color), 2.0))
        painter.drawLine(QPointF(left + 6.0, mid), QPointF(left + 26.0, mid))
        painter.setPen(QPen(QColor(TEXT_COLOR)))
        painter.drawText(QPointF(left + 30.0, y + metrics.ascent()), label)
        y += line_h


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


# ---------- 知识点关系图 ----------

# 关系图节点圆角与箭头尺寸（尺寸与间距见 graph_layout）
GRAPH_CORNER = 10.0
GRAPH_ARROW_SIZE = 10.0

# 各节点类型的 (填充色, 边框色, 文字色)，适配白底正文
GRAPH_NODE_STYLE: dict[str, tuple[str, str, str]] = {
    'root': ('#dbe6f6', '#5b8dd9', '#1b2a44'),
    'concept': ('#e6eef8', '#4a7db5', '#20303f'),
    'detail': ('#eef1f4', '#8a929b', '#333333'),
    'example': ('#e2f0d9', '#5f8a4a', '#2f3a2a'),
    'warning': ('#fbe4e4', '#d06a6a', '#5a2020'),
}
GRAPH_DEFAULT_NODE_STYLE = ('#eef1f4', '#8a929b', '#333333')

# 各边类型的 (颜色, 线型, 是否带箭头)
GRAPH_EDGE_STYLE: dict[str, tuple[str, Qt.PenStyle, bool]] = {
    'contains': ('#4a7db5', Qt.PenStyle.SolidLine, True),
    'hierarchy': ('#5f8a4a', Qt.PenStyle.SolidLine, True),
    'prerequisite': ('#b58a4a', Qt.PenStyle.SolidLine, True),
    'causes': ('#a04a4a', Qt.PenStyle.SolidLine, True),
    'contrast': ('#8a5aa0', Qt.PenStyle.DashLine, False),
    'related': ('#5a5a5a', Qt.PenStyle.DotLine, False),
}
GRAPH_DEFAULT_EDGE_STYLE = ('#5a5a5a', Qt.PenStyle.DotLine, False)


# 把 visualization 规格画成图片，节点按层级排布、有向边带箭头
def visualization_to_image(
    visualization: dict[str, Any],
    *,
    width: int = 760,
    max_height: int = 1100
) -> QImage | None:
    if not isinstance(visualization, dict):
        return None

    nodes = [n for n in (visualization.get('nodes') or []) if isinstance(n, dict)]
    edges = [e for e in (visualization.get('edges') or []) if isinstance(e, dict)]
    if not nodes:
        return None

    direction = str(visualization.get('direction') or 'top_down')
    positions = _graph_layout(_graph_levels(nodes, edges), nodes, direction)

    content_w, content_h = _graph_bounds(positions)
    scale = min(
        1.0,
        width / content_w if content_w else 1.0,
        max_height / content_h if content_h else 1.0
    )
    image = QImage(
        max(1, round(content_w * scale)),
        max(1, round(content_h * scale)),
        QImage.Format.Format_ARGB32
    )
    image.fill(QColor(BG_COLOR))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        painter.scale(scale, scale)
        _draw_graph_groups(painter, visualization.get('groups') or [], positions)
        _draw_graph_edges(painter, edges, positions)
        _draw_graph_nodes(painter, nodes, positions)
    finally:
        painter.end()

    return image


# 先给分组画虚线框，压在节点与连线下面
def _draw_graph_groups(
    painter: QPainter,
    groups: list[Any],
    positions: dict[str, QPointF]
) -> None:
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for group in groups:
        if not isinstance(group, dict):
            continue

        points = [
            positions[str(node_id)]
            for node_id in (group.get('node_ids') or [])
            if str(node_id) in positions
        ]
        if not points:
            continue

        left = min(point.x() for point in points) - 12.0
        top = min(point.y() for point in points) - 22.0
        right = max(point.x() for point in points) + GRAPH_NODE_WIDTH + 12.0
        bottom = max(point.y() for point in points) + GRAPH_NODE_HEIGHT + 12.0

        painter.setPen(QPen(QColor('#b9c2cc'), 1.2, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(QRectF(left, top, right - left, bottom - top), 8.0, 8.0)

        label = str(group.get('label') or '')
        if label:
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.setPen(QPen(QColor('#57606a')))
            painter.drawText(
                QRectF(left + 8.0, top + 3.0, right - left - 16.0, 16.0),
                Qt.AlignmentFlag.AlignLeft,
                label
            )


# 画全部边，后画的节点会压住线头
def _draw_graph_edges(
    painter: QPainter,
    edges: list[dict[str, Any]],
    positions: dict[str, QPointF]
) -> None:
    for edge in edges:
        start = positions.get(str(edge.get('source', '')))
        end = positions.get(str(edge.get('target', '')))
        if start is None or end is None:
            continue

        kind = str(edge.get('kind', 'related'))
        color, style, head = GRAPH_EDGE_STYLE.get(kind, GRAPH_DEFAULT_EDGE_STYLE)

        start_anchor, end_anchor = _edge_anchors(start, end)
        path, tip, tail = _edge_path(start_anchor, end_anchor)

        painter.setPen(QPen(QColor(color), 1.6, style))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if head:
            _draw_arrow(painter, QColor(color), tip, tail)

        label = str(edge.get('label') or '')
        if label:
            _draw_edge_label(painter, label, path.pointAtPercent(0.5), color)


# 在终点按切线方向画一个实心箭头
def _draw_arrow(
    painter: QPainter,
    color: QColor,
    tip: QPointF,
    tail: QPointF
) -> None:
    angle = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
    spread = 0.45
    left = QPointF(
        tip.x() - GRAPH_ARROW_SIZE * math.cos(angle - spread),
        tip.y() - GRAPH_ARROW_SIZE * math.sin(angle - spread)
    )
    right = QPointF(
        tip.x() - GRAPH_ARROW_SIZE * math.cos(angle + spread),
        tip.y() - GRAPH_ARROW_SIZE * math.sin(angle + spread)
    )

    painter.setPen(QPen(color, 1.0))
    painter.setBrush(QBrush(color))
    painter.drawPolygon(QPolygonF([tip, left, right]))


# 边的标签压在曲线上，垫一块白底保证可读
def _draw_edge_label(
    painter: QPainter,
    text: str,
    point: QPointF,
    color: str
) -> None:
    font = QFont()
    font.setPointSize(8)
    painter.setFont(font)

    metrics = painter.fontMetrics()
    width = metrics.horizontalAdvance(text)
    height = metrics.height()
    rect = QRectF(
        point.x() - width / 2.0 - 3.0,
        point.y() - height / 2.0 - 1.0,
        width + 6.0,
        height + 2.0
    )

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor('#ffffff')))
    painter.drawRoundedRect(rect, 3.0, 3.0)

    painter.setPen(QPen(QColor(color)))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


# 画全部节点，颜色与粗体由 kind 决定
def _draw_graph_nodes(
    painter: QPainter,
    nodes: list[dict[str, Any]],
    positions: dict[str, QPointF]
) -> None:
    for node in nodes:
        point = positions.get(str(node.get('id', '')))
        if point is None:
            continue

        kind = str(node.get('kind', 'concept'))
        fill, border, text_color = GRAPH_NODE_STYLE.get(kind, GRAPH_DEFAULT_NODE_STYLE)
        rect = QRectF(point.x(), point.y(), GRAPH_NODE_WIDTH, GRAPH_NODE_HEIGHT)

        painter.setPen(QPen(QColor(border), 1.6))
        painter.setBrush(QBrush(QColor(fill)))
        painter.drawRoundedRect(rect, GRAPH_CORNER, GRAPH_CORNER)

        font = QFont()
        font.setPointSize(10)
        font.setBold(kind == 'root')
        painter.setFont(font)
        painter.setPen(QPen(QColor(text_color)))
        painter.drawText(
            rect.adjusted(8.0, 4.0, -8.0, -4.0),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            str(node.get('label', ''))
        )