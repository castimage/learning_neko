# 全项目共享的枚举，以及把模型输出归一成枚举取值的校验器
from __future__ import annotations

from enum import StrEnum

from pydantic import BeforeValidator


# 把模型给出的枚举值统一成去空白、转小写、连字符换下划线的形式
def normalize_enum_value(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower().replace('-', '_').replace(' ', '_')
    return value


NormalizedEnum = BeforeValidator(normalize_enum_value)


# 学习阶段，只有学习中与学习完毕两个状态
class LearningPhase(StrEnum):
    LEARNING = 'learning'
    COMPLETED = 'completed'


# 学习者水平，取值与提示词里要求模型输出的中文一致
class LearnerLevel(StrEnum):
    BEGINNER = '入门'
    INTERMEDIATE = '进阶'
    ADVANCED = '熟练'


# 题目难度，用于命题时区分基础与进阶
class Difficulty(StrEnum):
    BASIC = '基础'
    ADVANCED = '进阶'


# 智能体种类，注册表以它为主键
class AgentKind(StrEnum):
    OUTLINE = 'outline'
    MATERIAL = 'material'
    QA = 'qa'
    CHECK = 'check'
    VERIFY = 'verify'
    SUMMARY = 'summary'


# 双模式智能体的阶段模式，单模式智能体不取此值
class AgentMode(StrEnum):
    PROCESS = 'process'
    POST = 'post'


# 结构化输出的绑定方式，对应langchain的三种method
class StructuredMode(StrEnum):
    JSON_SCHEMA = 'json_schema'
    JSON_MODE = 'json_mode'
    FUNCTION_CALLING = 'function_calling'


# 受校验产物的类型，决定适用哪一组审核点
class ArtifactKind(StrEnum):
    OUTLINE = 'outline'
    MATERIAL = 'material'
    VISUALIZATION = 'visualization'
    EXERCISES = 'exercises'


# 产物的最终处置结果
class ArtifactStatus(StrEnum):
    PASSED = 'passed'
    ACCEPTED_WITH_WARNING = 'accepted_with_warning'


# 校验结论来自确定性检查还是校验智能体
class VerificationSource(StrEnum):
    LOCAL = 'local'
    LLM = 'llm'


# 错题来自学习过程中的例题还是课后测验
class ExerciseStage(StrEnum):
    PROCESS = 'process'
    POST = 'post'


# 校验预算耗尽后的处置策略
class ExhaustedPolicy(StrEnum):
    RAISE = 'raise'
    ACCEPT_WITH_WARNING = 'accept_with_warning'


# 关系图节点类型，同时决定渲染时的层级与配色
class GraphNodeKind(StrEnum):
    ROOT = 'root'
    CONCEPT = 'concept'
    DETAIL = 'detail'
    EXAMPLE = 'example'
    WARNING = 'warning'


# 关系图边表达的语义类型，决定线条颜色
class GraphEdgeKind(StrEnum):
    HIERARCHY = 'hierarchy'
    CONTAINS = 'contains'
    PREREQUISITE = 'prerequisite'
    CAUSES = 'causes'
    CONTRAST = 'contrast'
    RELATED = 'related'


# 关系图的布局方向，供前端渲染器选择布局主轴
class GraphDirection(StrEnum):
    TOP_DOWN = 'top_down'
    LEFT_RIGHT = 'left_right'
    RADIAL = 'radial'


# 素材类型，前两种由前端按规格绘制，后三种从素材库取字节
class MediaKind(StrEnum):
    DIAGRAM = 'diagram'
    ANIMATION = 'animation'
    IMAGE = 'image'
    AUDIO = 'audio'
    VIDEO = 'video'


# 曲线线型，供前端绘制折线时选择
class CurveStyle(StrEnum):
    SOLID = 'solid'
    DASHED = 'dashed'
    DOTTED = 'dotted'
