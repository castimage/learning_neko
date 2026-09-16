# 状态迁移表与守卫，学习完毕不可回退且同一状态不可重复进入
from __future__ import annotations

from learning_neko.domain.errors import IllegalTransition, PhaseGuardViolation
from learning_neko.domain.models.enums import LearningPhase

ALLOWED_TRANSITIONS: dict[LearningPhase, tuple[LearningPhase, ...]] = {
    LearningPhase.LEARNING: (LearningPhase.COMPLETED,),
    LearningPhase.COMPLETED: ()
}

PHASE_ACTIONS: dict[LearningPhase, tuple[str, ...]] = {
    LearningPhase.LEARNING: ('generate_material', 'qa', 'check_example', 'complete'),
    LearningPhase.COMPLETED: ('generate_exercises', 'grade_exercises', 'summarize', 'read_report')
}


# 判断两个状态之间的迁移是否被允许
def can_transition(source: LearningPhase, target: LearningPhase) -> bool:
    return target in ALLOWED_TRANSITIONS[source]


# 列出某个状态下所有合法的目标状态
def allowed_transitions(source: LearningPhase) -> tuple[LearningPhase, ...]:
    return ALLOWED_TRANSITIONS[source]


# 列出某个状态下允许执行的操作，供接口层告知前端
def allowed_actions(phase: LearningPhase) -> tuple[str, ...]:
    return PHASE_ACTIONS[phase]


# 迁移不合法时抛出IllegalTransition
def assert_transition(source: LearningPhase, target: LearningPhase) -> None:
    if can_transition(source, target):
        return
    raise IllegalTransition(
        '状态迁移被拒绝：学习完毕不可回退，且同一状态不可重复进入',
        source=str(source),
        target=str(target),
        allowed=[str(item) for item in allowed_transitions(source)]
    )


# 当前阶段不允许该操作时抛出PhaseGuardViolation
def assert_action_allowed(phase: LearningPhase, action: str) -> None:
    permitted = allowed_actions(phase)
    if action in permitted:
        return
    raise PhaseGuardViolation(
        '当前学习阶段不允许该操作',
        current_phase=str(phase),
        action=action,
        allowed_actions=list(permitted)
    )
