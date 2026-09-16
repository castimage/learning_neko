# Markdown 渲染：把公式与 media 占位符转成内嵌图片，输出可交给 QTextBrowser 的 HTML
from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt

from desktop.widgets.math_renderer import render_formula_image
from desktop.widgets.media_assets import diagram_to_image, image_to_data_url, visualization_to_image

# 渲染实例：commonmark 打底，开启表格与删除线
MARKDOWN = MarkdownIt('commonmark').enable('table').enable('strikethrough')

# 正文里 media 占位符的形态：![图注](media:id)
MEDIA_PATTERN = re.compile(r'!\[([^\]]*)\]\(media:([A-Za-z0-9_\-]+)\)')
# 块级公式 $$...$$，允许跨行
BLOCK_FORMULA = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)
# 行内公式 $...$，不跨行且不与块级冲突
INLINE_FORMULA = re.compile(r'(?<!\$)\$(?!\$)([^\n$]+?)\$(?!\$)')

# 正文图片显示宽度（像素）
DIAGRAM_WIDTH = 720
DIAGRAM_HEIGHT = 320
# 关系图显示尺寸上限（像素）
GRAPH_WIDTH = 760
GRAPH_HEIGHT = 900
# 关系图没有 id 时用的兜底编号，不会被正文占位符命中
DEFAULT_GRAPH_ID = 'visualization'
# 行内公式超过这个长度就改用块级呈现，避免横向溢出
INLINE_FALLBACK_LIMIT = 24


# 把后端 markdown 渲染成适合 QTextBrowser 的 HTML
def render_markdown(
    markdown: str,
    media: list[dict[str, Any]] | None = None,
    visualization: dict[str, Any] | None = None,
    *,
    diagram_width: int = DIAGRAM_WIDTH,
    diagram_height: int = DIAGRAM_HEIGHT,
    graph_width: int = GRAPH_WIDTH,
    graph_height: int = GRAPH_HEIGHT
) -> str:
    by_id = _asset_index(media, visualization)

    text = drop_orphan_dollars(markdown)
    text = _replace_formulas(text)
    text = _replace_media(text, by_id, diagram_width, diagram_height, graph_width, graph_height)
    return MARKDOWN.render(text)


# 把 media 与顶层 visualization 归并成按 id 索引的素材表
# 目前后端产出的 visualization 不带 id，正文也没有关系图占位符，这里只是用兜底编号登记；
# 关系图实际由 study_page 追加到正文末尾。等后端给 visualization 加上 id、并在正文写
# ![关系图](media:<id>) 之后，下面 _replace_media 的 flowchart 分支会自动把图就地渲染，
# 届时无需再改前端。
def _asset_index(
    media: list[dict[str, Any]] | None,
    visualization: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    assets = {
        str(item.get('id')): item
        for item in (media or [])
        if isinstance(item, dict) and item.get('id')
    }

    if isinstance(visualization, dict) and visualization.get('nodes'):
        graph_id = str(visualization.get('id') or DEFAULT_GRAPH_ID)
        assets.setdefault(graph_id, {
            'id': graph_id,
            'kind': 'flowchart',
            'caption': str(visualization.get('title') or '知识点关系图'),
            'flowchart': visualization,
        })

    return assets


# 去掉孤立美元符：行内公式无法配对时会破坏后续解析
def drop_orphan_dollars(markdown: str) -> str:
    lines: list[str] = []

    for line in markdown.split('\n'):
        # 含块级标记的行整体跳过，不参与行内配对判断
        if '$$' in line:
            lines.append(line)
            continue

        # 奇数个美元符说明有孤立的，去掉最后一个，避免它吞掉后文的配对
        if line.count('$') % 2 == 1:
            index = line.rfind('$')
            line = line[:index] + line[index + 1:]

        lines.append(line)

    return '\n'.join(lines)


# 把 LaTeX 公式渲染成内联图片，渲染失败时降级成代码
def _replace_formulas(markdown: str) -> str:
    # 块级优先，避免 $$ 被行内规则拆坏
    def sub_block(match: re.Match[str]) -> str:
        latex = match.group(1).strip()
        url = _formula_url(latex, display=True)
        if url is None:
            return _fallback_block(latex)
        return f'\n\n![公式]({url})\n\n'

    def sub_inline(match: re.Match[str]) -> str:
        latex = match.group(1).strip()
        # 空内容保持原文，其余一律渲染，单字符公式（如 $B$）也必须处理
        if not latex:
            return match.group(0)
        url = _formula_url(latex, display=False)
        if url is None:
            return _fallback_inline(latex)
        return f'![公式]({url})'

    text = BLOCK_FORMULA.sub(sub_block, markdown)
    return INLINE_FORMULA.sub(sub_inline, text)


# 渲染公式并转成 data URL，失败返回 None
def _formula_url(latex: str, *, display: bool) -> str | None:
    image = render_formula_image(latex, display=display)
    if image is None:
        return None
    return image_to_data_url(image)


# 公式渲染失败：用代码块呈现源码，避免长公式挤成一行
def _fallback_block(latex: str) -> str:
    # 转义反引号，避免破坏代码块包裹
    safe = latex.replace('`', '\\`')
    return f'\n\n```latex\n{safe}\n```\n\n'


# 行内公式渲染失败：短的用行内代码，长的改单独成块
def _fallback_inline(latex: str) -> str:
    if len(latex) > INLINE_FALLBACK_LIMIT:
        return _fallback_block(latex)
    return f'`{latex}`'


# 把 media 占位符替换成内嵌图片或文字提示
def _replace_media(
    markdown: str,
    by_id: dict[str, dict[str, Any]],
    diagram_width: int,
    diagram_height: int,
    graph_width: int,
    graph_height: int
) -> str:
    def sub(match: re.Match[str]) -> str:
        alt = match.group(1).strip()
        media_id = match.group(2)
        item = by_id.get(media_id)

        # 正文引用了 media 里不存在的 id，降级成文字提示
        if item is None:
            return f'**【图】{alt or media_id}**'

        kind = str(item.get('kind') or '')

        # diagram 由客户端按规格绘制
        if kind == 'diagram':
            spec = item.get('diagram')
            if isinstance(spec, dict):
                image = diagram_to_image(
                    spec,
                    width=diagram_width,
                    height=diagram_height
                )
                if image is not None and not image.isNull():
                    caption = str(item.get('caption') or alt or media_id)
                    url = image_to_data_url(image)
                    return f'![{caption}]({url})　[查看详细图](chart:{media_id})'
            # 绘制规格缺失，退化成图注文字
            caption = str(item.get('caption') or alt or media_id)
            return f'**【图】{caption}**'

        # flowchart 由客户端把知识点关系画成图
        if kind == 'flowchart':
            spec = item.get('flowchart')
            if isinstance(spec, dict):
                image = visualization_to_image(
                    spec,
                    width=graph_width,
                    max_height=graph_height
                )
                if image is not None and not image.isNull():
                    caption = str(item.get('caption') or alt or media_id)
                    return f'![{caption}]({image_to_data_url(image)})'
            caption = str(item.get('caption') or alt or media_id)
            return f'**【图】{caption}**'

        # image/audio/video 需要从后端取字节，暂时给文字提示
        caption = str(item.get('caption') or alt or media_id)
        return f'**【{kind or "素材"}】{caption}**'

    return MEDIA_PATTERN.sub(sub, markdown)