# 知识点关系图交互视图：滚轮缩放、拖动平移、节点悬停看释义
from __future__ import annotations

import math
from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from desktop.widgets.graph_layout import (
    NODE_HEIGHT,
    NODE_WIDTH,
    assign_levels,
    edge_anchors,
    edge_path,
    layout_nodes,
)
from desktop.widgets.media_assets import (
    GRAPH_DEFAULT_EDGE_STYLE,
    GRAPH_DEFAULT_NODE_STYLE,
    GRAPH_EDGE_STYLE,
    GRAPH_NODE_STYLE,
)

# 圆角、箭头与缩放范围
CORNER_RADIUS = 10.0
ARROW_SIZE = 10.0
MIN_SCALE = 0.3
MAX_SCALE = 4.0
EMPTY_HINT = '本节暂无知识点关系图'
CANVAS_COLOR = '#ffffff'


# 节点：圆角矩形 + 居中折行文字，悬停显示释义
class NodeItem(QGraphicsPathItem):
    # 按 kind 取配色，记录文字与字体
    def __init__(self, node: dict[str, Any]) -> None:
        super().__init__()
        self.kind = str(node.get('kind', 'concept'))
        fill, border, text_color = GRAPH_NODE_STYLE.get(self.kind, GRAPH_DEFAULT_NODE_STYLE)
        self._fill = QColor(fill)
        self._border = QColor(border)
        self._text_color = QColor(text_color)

        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0.0, 0.0, NODE_WIDTH, NODE_HEIGHT),
            CORNER_RADIUS,
            CORNER_RADIUS
        )
        self.setPath(path)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_tooltip_of(node))

        self._label = str(node.get('label', ''))
        self._font = QFont()
        self._font.setPointSize(10)
        self._font.setBold(self.kind == 'root')

    # 自己画底色与文字，选中时只换边框色
    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(self._fill))
        if self.isSelected():
            painter.setPen(QPen(QColor('#ffb020'), 2.4))
        else:
            painter.setPen(QPen(self._border, 1.6))
        painter.drawPath(self.path())

        painter.setPen(QPen(self._text_color))
        painter.setFont(self._font)
        painter.drawText(
            self.boundingRect().adjusted(8.0, 4.0, -8.0, -4.0),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self._label
        )


# 边：曲线 + 有向箭头 + 标签
class EdgeItem(QGraphicsItem):
    # 保存绘制所需的一切，paint 时一次画出
    def __init__(
        self,
        path: QPainterPath,
        tip: QPointF,
        tail: QPointF,
        color: QColor,
        style: Qt.PenStyle,
        head: bool,
        label: str
    ) -> None:
        super().__init__()
        self._path = path
        self._tip = tip
        self._tail = tail
        self._color = color
        self._style = style
        self._head = head
        self._label = label

    # 包围盒放宽以覆盖箭头与标签
    def boundingRect(self) -> QRectF:
        return self._path.boundingRect().adjusted(-40.0, -24.0, 40.0, 24.0)

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(self._color, 1.6, self._style))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)

        if self._head:
            _paint_arrow(painter, self._color, self._tip, self._tail)

        if self._label:
            _paint_edge_label(painter, self._label, self._path.pointAtPercent(0.5), self._color)


# 在终点按切线方向画实心箭头
def _paint_arrow(painter: QPainter, color: QColor, tip: QPointF, tail: QPointF) -> None:
    angle = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
    spread = 0.45
    left = QPointF(
        tip.x() - ARROW_SIZE * math.cos(angle - spread),
        tip.y() - ARROW_SIZE * math.sin(angle - spread)
    )
    right = QPointF(
        tip.x() - ARROW_SIZE * math.cos(angle + spread),
        tip.y() - ARROW_SIZE * math.sin(angle + spread)
    )

    painter.setPen(QPen(color, 1.0))
    painter.setBrush(QBrush(color))
    painter.drawPolygon(QPolygonF([tip, left, right]))


# 边标签垫一块半透明白底保证可读
def _paint_edge_label(painter: QPainter, text: str, point: QPointF, color: QColor) -> None:
    font = QFont()
    font.setPointSize(8)
    painter.setFont(font)

    metrics = painter.fontMetrics()
    width = metrics.horizontalAdvance(text)
    height = metrics.height()
    rect = QRectF(
        point.x() - width / 2.0 - 4.0,
        point.y() - height / 2.0 - 2.0,
        width + 8.0,
        height + 4.0
    )

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(255, 255, 255, 230)))
    painter.drawRoundedRect(rect, 4.0, 4.0)

    painter.setPen(QPen(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


# 节点提示：名称加释义
def _tooltip_of(node: dict[str, Any]) -> str:
    label = str(node.get('label', ''))
    detail = str(node.get('detail', ''))
    return f'{label}\n\n{detail}' if detail else label


# 概念关系图的只读交互视图
class ConceptGraphView(QGraphicsView):
    # 初始化场景、拖拽与缩放锚点
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setStyleSheet(f'background-color: {CANVAS_COLOR}; border: none;')
        self.render_graph({})

    # 清空场景
    def clear_graph(self) -> None:
        self._scene.clear()

    # 用 visualization 数据重建整张图
    def render_graph(self, visualization: dict[str, Any]) -> None:
        self.clear_graph()

        nodes = _dict_list(visualization.get('nodes')) if isinstance(visualization, dict) else []
        edges = _dict_list(visualization.get('edges')) if isinstance(visualization, dict) else []
        if not nodes:
            self._show_empty()
            return

        direction = str(visualization.get('direction') or 'top_down')
        positions = layout_nodes(assign_levels(nodes, edges), nodes, direction)

        self._draw_groups(visualization.get('groups') or [], positions)
        self._draw_edges(edges, positions)
        self._draw_nodes(nodes, positions)

        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-24.0, -24.0, 24.0, 24.0))
        self._fit()

    # 无数据时居中显示提示
    def _show_empty(self) -> None:
        text = QGraphicsSimpleTextItem(EMPTY_HINT)
        font = QFont()
        font.setPointSize(11)
        text.setFont(font)
        text.setBrush(QBrush(QColor('#888888')))

        bounds = text.boundingRect()
        text.setPos(-bounds.width() / 2.0, -bounds.height() / 2.0)
        self._scene.addItem(text)

        self._scene.setSceneRect(bounds.adjusted(-40.0, -40.0, 40.0, 40.0))
        self._fit()

    def _draw_nodes(self, nodes: list[dict[str, Any]], positions: dict[str, QPointF]) -> None:
        for node in nodes:
            point = positions.get(str(node.get('id', '')))
            if point is None:
                continue
            item = NodeItem(node)
            item.setPos(point)
            self._scene.addItem(item)

    def _draw_edges(self, edges: list[dict[str, Any]], positions: dict[str, QPointF]) -> None:
        for edge in edges:
            start = positions.get(str(edge.get('source', '')))
            end = positions.get(str(edge.get('target', '')))
            if start is None or end is None:
                continue

            kind = str(edge.get('kind', 'related'))
            color, style, head = GRAPH_EDGE_STYLE.get(kind, GRAPH_DEFAULT_EDGE_STYLE)
            start_anchor, end_anchor = edge_anchors(start, end)
            path, tip, tail = edge_path(start_anchor, end_anchor)

            item = EdgeItem(path, tip, tail, QColor(color), style, head, str(edge.get('label') or ''))
            item.setZValue(-1.0)
            self._scene.addItem(item)

    def _draw_groups(self, groups: list[Any], positions: dict[str, QPointF]) -> None:
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

            left = min(point.x() for point in points) - 14.0
            top = min(point.y() for point in points) - 24.0
            right = max(point.x() for point in points) + NODE_WIDTH + 14.0
            bottom = max(point.y() for point in points) + NODE_HEIGHT + 14.0

            frame = QGraphicsRectItem(QRectF(left, top, right - left, bottom - top))
            frame.setPen(QPen(QColor('#b9c2cc'), 1.2, Qt.PenStyle.DashLine))
            frame.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            frame.setZValue(-2.0)
            self._scene.addItem(frame)

            label = str(group.get('label') or '')
            if label:
                text = QGraphicsSimpleTextItem(label)
                font = QFont()
                font.setPointSize(8)
                text.setFont(font)
                text.setBrush(QBrush(QColor('#57606a')))
                text.setPos(left + 8.0, top + 3.0)
                text.setZValue(-1.5)
                self._scene.addItem(text)

    # 适配窗口
    def _fit(self) -> None:
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # 滚轮缩放，限制在合理范围
    def wheelEvent(self, event: Any) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return

        factor = 1.15 if delta > 0 else 1.0 / 1.15
        target = self.transform().m11() * factor
        if MIN_SCALE <= target <= MAX_SCALE:
            self.scale(factor, factor)
        event.accept()

    # 双击回到适配窗口
    def mouseDoubleClickEvent(self, event: Any) -> None:
        self._fit()


# 取出列表里的字典项，过滤脏数据
def _dict_list(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]
