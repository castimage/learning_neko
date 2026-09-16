# 学习报告渲染：把归档报告数据拼成可渲染的 markdown，多个页面共用
from __future__ import annotations

from typing import Any


# 用方块拼出掌握度条
def mastery_bar(value: float) -> str:
    filled = max(0, min(10, round(value * 10)))
    return '█' * filled + '░' * (10 - filled)


# 把 SummaryView 数据拼成 markdown
def report_to_markdown(data: dict[str, Any]) -> str:
    report = str(data.get('report') or '')
    score = str(data.get('score') or '')
    comment = str(data.get('comment') or '')
    mastery = data.get('mastery') or {}
    next_focus = data.get('next_focus') or []

    parts: list[str] = []
    if score or comment:
        parts.append('## 得分')
        parts.append(f'**{score}**　{comment}'.strip())
    if report:
        parts.append('## 学习报告')
        parts.append(report)
    if mastery:
        parts.append('## 掌握度')
        for point, value in mastery.items():
            parts.append(f'- {point}：{mastery_bar(float(value))} {float(value):.0%}')
    if next_focus:
        parts.append('## 下次重点')
        parts.extend(f'- {item}' for item in next_focus)

    return '\n\n'.join(parts)
