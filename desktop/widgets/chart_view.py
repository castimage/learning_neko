# 折线图交互视图：滚轮缩放、拖动平移，矢量绘制缩放不糊
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from desktop.widgets.media_assets import BG_COLOR, paint_diagram

# 交互画布尺寸、缩放范围与空提示
CHART_WIDTH = 900
CHART_HEIGHT = 560
MIN_SCALE = 0.3
MAX_SCALE = 5.0
EMPTY_HINT = '点资料里的「查看详细图」打开折线图'


# 图表项：把 diagram 画成矢量图，随视图缩放保持清晰
class ChartItem(QGraphicsItem):
    # 保存规格
    def __init__(self, diagram: dict[str, Any]) -> None:
        super().__init__()
        self._diagram = diagram

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, CHART_WIDTH, CHART_HEIGHT)

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.boundingRect(), QBrush(QColor(BG_COLOR)))
        paint_diagram(painter, self._diagram, CHART_WIDTH, CHART_HEIGHT)


# 折线图只读交互视图
class ChartView(QGraphicsView):
    # 初始化场景、拖拽与缩放锚点
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setStyleSheet(f'background-color: {BG_COLOR}; border: none;')
        self.render_chart({})

    # 清空场景
    def clear_chart(self) -> None:
        self._scene.clear()

    # 用 diagram 数据重建图表
    def render_chart(self, diagram: dict[str, Any]) -> None:
        self.clear_chart()
        if not _has_series(diagram):
            self._show_empty()
            return

        item = ChartItem(diagram)
        self._scene.addItem(item)
        self._scene.setSceneRect(item.boundingRect().adjusted(-20.0, -20.0, 20.0, 20.0))
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

    # 适配窗口
    def _fit(self) -> None:
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # 滚轮缩放，把结果夹在合理区间
    def wheelEvent(self, event: Any) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return

        current = self.transform().m11()
        if current <= 0:
            return

        factor = 1.15 if delta > 0 else 1.0 / 1.15
        target = current * factor
        if target < MIN_SCALE:
            factor = MIN_SCALE / current
        elif target > MAX_SCALE:
            factor = MAX_SCALE / current

        self.scale(factor, factor)
        event.accept()

    # 双击回到适配窗口
    def mouseDoubleClickEvent(self, event: Any) -> None:
        self._fit()


# 至少要有一条带点的曲线才值得画
def _has_series(diagram: Any) -> bool:
    if not isinstance(diagram, dict):
        return False
    return any(
        isinstance(series, dict) and series.get('points')
        for series in (diagram.get('series') or [])
    )
