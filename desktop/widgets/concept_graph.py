# 概念关系图：把后端的 visualization 数据画成层次图，支持点选节点看详情
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

# 各 kind 的配色与圆角，决定节点的视觉层次
NODE_STYLE: dict[str, tuple[str, str, str]] = {
    # kind: (填充色, 边框色, 文字色)
    'root': ('#2d3a52', '#5b8dd9', '#e8eefc'),
    'concept': ('#243044', '#4a7db5', '#d8e4f2'),
    'detail': ('#2a2a2a', '#5a5a5a', '#c8c8c8'),
    'example': ('#2f3a2a', '#5f8a4a', '#d6e8c8'),
    'warning': ('#3a2a2a', '#a04a4a', '#f0d0d0'),
}

# 各边类型的线型与颜色
EDGE_STYLE: dict[str, tuple[str, Qt.PenStyle, bool]] = {
    # kind: (颜色, 线型, 是否带头)
    'contains': ('#4a7db5', Qt.PenStyle.SolidLine, True),
    'hierarchy': ('#5f8a4a', Qt.PenStyle.SolidLine, True),
    'prerequisite': ('#b58a4a', Qt.PenStyle.SolidLine, True),
    'causes': ('#a04a4a', Qt.PenStyle.SolidLine, True),
    'contrast': ('#8a5aa0', Qt.PenStyle.DashLine, False),
    'related': ('#5a5a5a', Qt.PenStyle.DotLine, False),
}

DEFAULT_NODE_STYLE = ('#2a2a2a', '#555555', '#d0d0d0')
DEFAULT_EDGE_STYLE = ('#5a5a5a', Qt.PenStyle.DotLine, False)

# 布局尺寸
NODE_WIDTH = 190.0
NODE_HEIGHT = 62.0
H_GAP = 34.0
V_GAP = 96.0
MARGIN = 40.0

# 参与分层的边类型，其余边视为引用不决定层级
LAYERING_EDGES = frozenset({'contains', 'hierarchy', 'prerequisite', 'causes'})

# 节点圆角半径
CORNER_RADIUS = 10.0


# 一个节点的绘制项
class GraphNodeItem(QGraphicsPathItem):
    # 保存节点数据与原始色板，供选中时高亮
    def __init__(self, node: dict[str, Any]) -> None:
        super().__init__()
        self.node = node
        self.node_id = str(node.get('id', ''))
        self.kind = str(node.get('kind', 'concept'))

        fill, border, text_color = NODE_STYLE.get(self.kind, DEFAULT_NODE_STYLE)
        self._fill = QColor(fill)
        self._border = QColor(border)
        self._text_color = text_color

        self.setPath(_rounded_rect(0.0, 0.0, NODE_WIDTH, NODE_HEIGHT, CORNER_RADIUS))
        self.setBrush(QBrush(self._fill))
        self.setPen(QPen(self._border, 1.6))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(_tooltip_of(node))

        # 节点标题，过长时手动折行
        self._label = QGraphicsSimpleTextItem(_wrap(str(node.get('label', '')), 14), self)
        font = QFont()
        font.setPointSize(10)
        font.setBold(self.kind == 'root')
        self._label.setFont(font)
        self._label.setBrush(QBrush(QColor(text_color)))
        label_rect = self._label.boundingRect()
        self._label.setPos(
            (NODE_WIDTH - label_rect.width()) / 2.0,
            (NODE_HEIGHT - label_rect.height()) / 2.0
        )

    # 选中或悬停时加粗边框
    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(self._fill))
        if self.isSelected():
            painter.setPen(QPen(QColor('#ffd479'), 2.6))
        else:
            painter.setPen(QPen(self._border, 1.6))
        painter.drawPath(self.path())
        painter.setPen(QPen(QColor(self._text_color)))
        painter.setFont(self._label.font())
        painter.setBrush(QBrush(QColor(self._text_color)))

        # 自己画文字，避免选中态下颜色被系统样式覆盖
        metrics = painter.fontMetrics()
        text = self._label.text()
        lines = text.split('\n')
        line_h = metrics.height()
        total_h = line_h * len(lines)
        y = (NODE_HEIGHT - total_h) / 2.0 + metrics.ascent()
        for line in lines:
            w = metrics.horizontalAdvance(line)
            painter.drawText(QPointF((NODE_WIDTH - w) / 2.0, y), line)
            y += line_h


# 一条边的绘制项
class GraphEdgeItem(QGraphicsPathItem):
    # 保存边数据，供弹提示
    def __init__(self, edge: dict[str, Any]) -> None:
        super().__init__()
        self.edge = edge
        kind = str(edge.get('kind', 'related'))
        color, style, _ = EDGE_STYLE.get(kind, DEFAULT_EDGE_STYLE)
        self.setPen(QPen(QColor(color), 1.4, style))
        self.setZValue(-1.0)
        label = str(edge.get('label', ''))
        if label:
            self.setToolTip(f'{kind}：{label}')


# 概念关系图的只读视图
class ConceptGraphView(QGraphicsView):
    # 初始化场景与缩放状态
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setStyleSheet('background-color: #1b1b1b; border: none;')
        self._nodes: dict[str, GraphNodeItem] = {}

    # 清空场景
    def clear_graph(self) -> None:
        self._scene.clear()
        self._nodes = {}

    # 用 visualization 数据重建整张图
    def render_graph(self, visualization: dict[str, Any]) -> None:
        self.clear_graph()
        nodes = [n for n in (visualization.get('nodes') or []) if isinstance(n, dict)]
        edges = [e for e in (visualization.get('edges') or []) if isinstance(e, dict)]
        if not nodes:
            return

        levels = _assign_levels(nodes, edges)
        positions = _layout(levels, nodes)
        self._draw_edges(edges, positions)
        self._draw_nodes(nodes, positions)

        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(
            -MARGIN, -MARGIN, MARGIN, MARGIN
        ))
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # 先画边，保证节点压在上层
    def _draw_edges(
        self,
        edges: list[dict[str, Any]],
        positions: dict[str, QPointF]
    ) -> None:
        for edge in edges:
            source = str(edge.get('source', ''))
            target = str(edge.get('target', ''))
            if source not in positions or target not in positions:
                continue
            start = positions[source]
            end = positions[target]
            item = GraphEdgeItem(edge)
            item.setPath(_curve_between(start, end))
            self._scene.addItem(item)

    # 再画节点
    def _draw_nodes(
        self,
        nodes: list[dict[str, Any]],
        positions: dict[str, QPointF]
    ) -> None:
        for node in nodes:
            node_id = str(node.get('id', ''))
            point = positions.get(node_id)
            if point is None:
                continue
            item = GraphNodeItem(node)
            item.setPos(point)
            self._scene.addItem(item)
            self._nodes[node_id] = item

    # 滚轮缩放，按住拖动平移
    def wheelEvent(self, event: Any) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)

    # 双击空白重新适配窗口
    def mouseDoubleClickEvent(self, event: Any) -> None:
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


# 取出所有节点的 id 与出边，按最长路径分层
def _assign_levels(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]]
) -> dict[str, int]:
    ids = [str(n.get('id', '')) for n in nodes]
    idset = set(ids)

    # 只保留参与分层的边
    layering = [
        (str(e.get('source')), str(e.get('target')))
        for e in edges
        if str(e.get('kind', '')) in LAYERING_EDGES
        and str(e.get('source')) in idset
        and str(e.get('target')) in idset
    ]

    outgoing: dict[str, list[str]] = {i: [] for i in ids}
    indegree: dict[str, int] = {i: 0 for i in ids}
    for source, target in layering:
        outgoing[source].append(target)
        indegree[target] += 1

    levels: dict[str, int] = {i: 0 for i in ids}
    queue = [i for i in ids if indegree[i] == 0]

    # 按拓扑序遍历，取最长路径长度作为层级
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for target in outgoing[current]:
            levels[target] = max(levels[target], levels[current] + 1)
            indegree[target] -= 1
            if indegree[target] <= 0:
                queue.append(target)

    # 未参与分层的节点（如只被 related 指向的示例）统一放到最底层
    for i in ids:
        if i not in visited:
            levels[i] = max(levels.values()) + 1 if levels else 0

    return levels


# 按层级与层内顺序算坐标，返回左上角位置
def _layout(
    levels: dict[str, int],
    nodes: list[dict[str, Any]]
) -> dict[str, QPointF]:
    # 按层级分组，保持 nodes 里的原始顺序以稳定输出
    buckets: dict[int, list[str]] = {}
    for node in nodes:
        node_id = str(node.get('id', ''))
        buckets.setdefault(levels.get(node_id, 0), []).append(node_id)

    # 最宽的一层决定画布宽度，用于整体居中
    widest = max((len(v) for v in buckets.values()), default=1)
    canvas_width = widest * NODE_WIDTH + (widest - 1) * H_GAP
    positions: dict[str, QPointF] = {}

    for level in sorted(buckets):
        members = buckets[level]
        row_width = len(members) * NODE_WIDTH + (len(members) - 1) * H_GAP
        offset = (canvas_width - row_width) / 2.0
        y = MARGIN + level * (NODE_HEIGHT + V_GAP)
        for index, node_id in enumerate(members):
            x = MARGIN + offset + index * (NODE_WIDTH + H_GAP)
            positions[node_id] = QPointF(x, y)

    return positions


# 生成圆角矩形路径
def _rounded_rect(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


# 用三次贝塞尔连接两点，纵向图走竖直曲线
def _curve_between(start: QPointF, end: QPointF) -> QPainterPath:
    path = QPainterPath()
    top = QPointF(start.x() + NODE_WIDTH / 2.0, start.y() + NODE_HEIGHT)
    bottom = QPointF(end.x() + NODE_WIDTH / 2.0, end.y())

    # 起点在终点下方时改为从节点顶部出发，避免线穿过节点
    if bottom.y() < top.y():
        top = QPointF(start.x() + NODE_WIDTH / 2.0, start.y())
        bottom = QPointF(end.x() + NODE_WIDTH / 2.0, end.y() + NODE_HEIGHT)

    path.moveTo(top)
    mid_y = (top.y() + bottom.y()) / 2.0
    path.cubicTo(
        QPointF(top.x(), mid_y),
        QPointF(bottom.x(), mid_y),
        bottom
    )
    return path


# 按长度折行，尽量保持中文可读
def _wrap(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    lines: list[str] = []
    current = ''
    for char in text:
        current += char
        if len(current) >= width:
            lines.append(current)
            current = ''
    if current:
        lines.append(current)
    return '\n'.join(lines[:3])


# 拼出节点提示，包含类型与说明
def _tooltip_of(node: dict[str, Any]) -> str:
    label = str(node.get('label', ''))
    kind = str(node.get('kind', ''))
    detail = str(node.get('detail', ''))
    return f'{label}（{kind}）\n\n{detail}' if detail else f'{label}（{kind}）'