# 学习内容与判定结果的领域模型，同时充当智能体的结构化输出契约
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from learning_neko.domain.models.enums import (
    CurveStyle,
    Difficulty,
    GraphDirection,
    GraphEdgeKind,
    GraphNodeKind,
    LearnerLevel,
    MediaKind,
    NormalizedEnum
)

NodeKind = Annotated[GraphNodeKind, NormalizedEnum]
EdgeKind = Annotated[GraphEdgeKind, NormalizedEnum]
Direction = Annotated[GraphDirection, NormalizedEnum]
MediaType = Annotated[MediaKind, NormalizedEnum]
Curve = Annotated[CurveStyle, NormalizedEnum]


# 大纲里的一个分节，含标题、可检验的目标与概念词
class OutlineSection(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str
    goal: str
    keywords: list[str]


# 一次学习的大纲，主题名同时用作跨会话记忆的检索键
class Outline(BaseModel):
    model_config = ConfigDict(extra='forbid')

    topic: str
    learner_level: Annotated[LearnerLevel, NormalizedEnum]
    key_points: list[str]
    outline: list[OutlineSection]
    gen_instruction: str


# 示意图的坐标轴，显式给出范围前端才能做无歧义的线性映射
class PlotAxis(BaseModel):
    model_config = ConfigDict(extra='forbid')

    x_label: str
    y_label: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float


# 按坐标落笔的标注
class PlotMark(BaseModel):
    model_config = ConfigDict(extra='forbid')

    text: str
    x: float
    y: float


# 曲线上的一个采样点
class CurvePoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    x: float
    y: float


# 一条曲线
class CurveSeries(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    label: str
    style: Curve = CurveStyle.SOLID
    points: list[CurvePoint]


# 一张由前端绘制的示意图
class DiagramSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str
    axis: PlotAxis
    series: list[CurveSeries]
    marks: list[PlotMark] = Field(default_factory=list)


# 动画里的一条被观测曲线
class MotionTrack(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    label: str


# 某条曲线在某一帧的取值
class MotionSample(BaseModel):
    model_config = ConfigDict(extra='forbid')

    track_id: str
    value: float


# 动画的一帧，t为相对时间轴上的位置
class MotionFrame(BaseModel):
    model_config = ConfigDict(extra='forbid')

    t: float
    samples: list[MotionSample]


# 动画的播放参数，供前端定时器推进播放头
class PlaybackSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    duration_ms: int
    fps: int = 30
    loop: bool = True
    autoplay: bool = True


# 一段由前端绘制并按时间轴播放的动画
class AnimationSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str
    axis: PlotAxis
    tracks: list[MotionTrack]
    frames: list[MotionFrame]
    playback: PlaybackSpec
    marks: list[PlotMark] = Field(default_factory=list)


# 一份素材，可画的自带规格，画不出的给素材库的key
class MediaAsset(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    kind: MediaType
    caption: str
    alt: str | None = ''
    asset_key: str | None = ''
    diagram: DiagramSpec | None = None
    animation: AnimationSpec | None = None


# 单道题目，options为空数组时表示填空题
class Exercise(BaseModel):
    model_config = ConfigDict(extra='forbid')

    q_id: str
    question: str
    options: list[str] = Field(default_factory=list)
    answer: str
    analysis: str | None = ''
    knowledge_point: str | None = ''
    difficulty: Annotated[Difficulty, NormalizedEnum] | None = None
    media: list[MediaAsset] = Field(default_factory=list)


# 课后测验的题目集合
class ExerciseSet(BaseModel):
    model_config = ConfigDict(extra='forbid')

    exercises: list[Exercise]


# 学习者对单道题目的一次作答
class AnswerSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid')

    q_id: str
    answer: str


# 过程检测里单道题的判定
class CheckResultItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    q_id: str
    correct: bool
    reason: str


# 过程检测的逐题判定集合
class CheckResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    results: list[CheckResultItem]


# 课后批阅里单道题的判定，比过程检测多一条改进建议
class GradeResultItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    q_id: str
    correct: bool
    reason: str
    advice: str


# 课后批阅的逐题判定集合
class GradeResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    results: list[GradeResultItem]


# 关系图的一个节点
class GraphNode(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    label: str
    kind: NodeKind
    detail: str | None = ''


# 关系图的一条边，两端必须指向已声明的节点编号
class GraphEdge(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source: str
    target: str
    kind: EdgeKind
    label: str | None = ''


# 把相关节点归为一组的显示分组
class GraphGroup(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    label: str
    node_ids: list[str] = Field(default_factory=list)


# 一张知识点关系图的完整规格，与任何具体渲染器解耦
class VisualizationSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str | None = ''
    title: str
    direction: Direction
    nodes: list[GraphNode]
    edges: list[GraphEdge] = Field(default_factory=list)
    groups: list[GraphGroup] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# 一个分节的生成产物，含正文、配套例题与关系图
class MaterialBundle(BaseModel):
    model_config = ConfigDict(extra='forbid')

    material: str
    examples: list[Exercise] = Field(default_factory=list)
    visualization: VisualizationSpec
    media: list[MediaAsset] = Field(default_factory=list)


# 一次答疑留下的疑点记录，point需可直接用于跨会话检索
class DoubtRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    point: str
    question: str
    explained: bool


# 给学习者的回答，以及本次疑点的记录
class QaAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')

    answer: str
    record: DoubtRecord
