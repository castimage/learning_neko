# HTTP 客户端包，对外只暴露客户端与异常，不依赖任何 Qt 组件
from desktop.api.client import ApiError, LearningClient

__version__ = '0.1.1'
__all__ = ['ApiError', 'LearningClient']