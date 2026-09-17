# LaTeX 公式渲染：用 matplotlib 的 mathtext 出图，Qt 富文本本身不支持公式
from __future__ import annotations

from PyQt6.QtGui import QImage

# 公式前景色，适配白底
FOREGROUND = '#1f2937'
# 公式图片底色
BG_COLOR = '#ffffff'
# 渲染放大倍数，越大越清晰
SCALE = 3.0
# 公式图片缓存上限，超过按插入顺序淘汰，避免长会话无界增长
CACHE_LIMIT = 256
# 公式图片缓存，None 表示该公式渲染失败，避免重复尝试
_CACHE: dict[str, QImage | None] = {}
# matplotlib 可用性，只在首次调用时探测
_AVAILABLE: bool | None = None


# 探测 matplotlib 是否可用
def mathtext_available() -> bool:
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            from matplotlib.mathtext import MathTextParser  # noqa: F401
            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


# 清空公式缓存，供测试或配置变更后调用
def clear_cache() -> None:
    _CACHE.clear()


# 把 LaTeX 渲染成透明底图片，不可用或失败时返回 None
def render_formula_image(latex: str, *, display: bool) -> QImage | None:
    source = latex.strip()
    if not source:
        return None

    key = f'{int(display)}:{source}'
    if key in _CACHE:
        return _CACHE[key]

    image = _render(source, display=display)
    if len(_CACHE) >= CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[key] = image
    return image


# 调 matplotlib 出 PNG，再读成 QImage
def _render(source: str, *, display: bool) -> QImage | None:
    if not mathtext_available():
        return None

    try:
        import io

        from matplotlib.figure import Figure
        from matplotlib.mathtext import MathTextParser
    except Exception:
        return None

    # mathtext 需要美元符号包裹来识别公式
    expression = f'${source}$'
    parser = MathTextParser('path')

    # 先量尺寸再开画布，避免浪费
    try:
        measured = parser.parse(expression, dpi=72.0)
    except Exception:
        return None

    if not measured or len(measured) < 2:
        return None

    width = float(measured[0])
    height = float(measured[1])
    if width <= 1.0 or height <= 1.0:
        return None

    dpi = 72.0 * SCALE
    # 四周留白，避免公式贴边被裁
    fig_w = (width + 18.0) / 72.0
    fig_h = (height + 18.0) / 72.0

    try:
        figure = Figure(figsize=(fig_w, fig_h), dpi=dpi)
        figure.patch.set_facecolor(BG_COLOR)
        figure.patch.set_alpha(1.0)
        # 块级公式字号略大，行内跟随正文
        fontsize = 13.0 if display else 11.0
        figure.text(
            0.5, 0.5, expression,
            fontsize=fontsize,
            color=FOREGROUND,
            va='center',
            ha='center'
        )
    except Exception:
        return None

    buffer = io.BytesIO()
    try:
        figure.savefig(buffer, format='png', facecolor=BG_COLOR, transparent=False)
    except Exception:
        return None

    image = QImage()
    if not image.loadFromData(buffer.getvalue(), 'PNG'):
        return None

    return image