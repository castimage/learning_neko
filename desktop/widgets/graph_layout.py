# 知识点关系图的通用布局：分层与坐标计算，图片渲染与交互视图共用
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainterPath

# 节点尺寸与间距
NODE_WIDTH = 190.0
NODE_HEIGHT = 62.0
H_GAP = 40.0
V_GAP = 88.0
MARGIN = 30.0

# 参与分层的边类型，其余边只影响连线不影响层级
LAYERING_EDGES = frozenset({'contains', 'hierarchy', 'prerequisite', 'causes'})


# 按最长路径分层，未参与分层的节点压到最底层
def assign_levels(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]]
) -> dict[str, int]:
    ids = [str(n.get('id', '')) for n in nodes]
    idset = set(ids)

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

    for i in ids:
        if i not in visited:
            levels[i] = max(levels.values()) + 1 if levels else 0

    return levels


# 按层级与层内顺序算坐标，left_right 横排，其余自上而下
def layout_nodes(
    levels: dict[str, int],
    nodes: list[dict[str, Any]],
    direction: str
) -> dict[str, QPointF]:
    buckets: dict[int, list[str]] = {}
    for node in nodes:
        node_id = str(node.get('id', ''))
        buckets.setdefault(levels.get(node_id, 0), []).append(node_id)

    positions: dict[str, QPointF] = {}

    if direction == 'left_right':
        widest = max((len(v) for v in buckets.values()), default=1)
        canvas_h = widest * NODE_HEIGHT + (widest - 1) * H_GAP
        for level in sorted(buckets):
            members = buckets[level]
            column_h = len(members) * NODE_HEIGHT + (len(members) - 1) * H_GAP
            offset = (canvas_h - column_h) / 2.0
            x = MARGIN + level * (NODE_WIDTH + H_GAP)
            for index, node_id in enumerate(members):
                y = MARGIN + offset + index * (NODE_HEIGHT + H_GAP)
                positions[node_id] = QPointF(x, y)
        return positions

    widest = max((len(v) for v in buckets.values()), default=1)
    canvas_w = widest * NODE_WIDTH + (widest - 1) * H_GAP
    for level in sorted(buckets):
        members = buckets[level]
        row_w = len(members) * NODE_WIDTH + (len(members) - 1) * H_GAP
        offset = (canvas_w - row_w) / 2.0
        y = MARGIN + level * (NODE_HEIGHT + V_GAP)
        for index, node_id in enumerate(members):
            x = MARGIN + offset + index * (NODE_WIDTH + H_GAP)
            positions[node_id] = QPointF(x, y)

    return positions


# 内容包围盒尺寸，供画布或视图适配使用
def content_bounds(positions: dict[str, QPointF]) -> tuple[float, float]:
    if not positions:
        return MARGIN * 2 + NODE_WIDTH, MARGIN * 2 + NODE_HEIGHT

    max_x = max(point.x() for point in positions.values())
    max_y = max(point.y() for point in positions.values())
    return max_x + NODE_WIDTH + MARGIN, max_y + NODE_HEIGHT + MARGIN


# 从节点中心判断走横线还是竖线，取对应边上的锚点
def edge_anchors(start: QPointF, end: QPointF) -> tuple[QPointF, QPointF]:
    start_x = start.x() + NODE_WIDTH / 2.0
    start_y = start.y() + NODE_HEIGHT / 2.0
    end_x = end.x() + NODE_WIDTH / 2.0
    end_y = end.y() + NODE_HEIGHT / 2.0

    if abs(end_x - start_x) >= abs(end_y - start_y):
        if end_x >= start_x:
            return QPointF(start.x() + NODE_WIDTH, start_y), QPointF(end.x(), end_y)
        return QPointF(start.x(), start_y), QPointF(end.x() + NODE_WIDTH, end_y)

    if end_y >= start_y:
        return QPointF(start_x, start.y() + NODE_HEIGHT), QPointF(end_x, end.y())
    return QPointF(start_x, start.y()), QPointF(end_x, end.y() + NODE_HEIGHT)


# 两锚点间画三次贝塞尔，返回路径、箭头落点与切线基点
def edge_path(start: QPointF, end: QPointF) -> tuple[QPainterPath, QPointF, QPointF]:
    path = QPainterPath()
    path.moveTo(start)

    if abs(end.x() - start.x()) >= abs(end.y() - start.y()):
        mid_x = (start.x() + end.x()) / 2.0
        control = QPointF(mid_x, end.y())
        path.cubicTo(QPointF(mid_x, start.y()), control, end)
    else:
        mid_y = (start.y() + end.y()) / 2.0
        control = QPointF(end.x(), mid_y)
        path.cubicTo(QPointF(start.x(), mid_y), control, end)

    return path, end, control
