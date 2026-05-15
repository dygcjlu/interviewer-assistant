"""业务异常定义。

所有自定义异常均继承 ``InterviewAssistantError``。各层在抛出错误时
应选择最贴近语义的子类，禁止直接抛出 ``ValueError`` 等内建异常作为
业务错误。
"""
from __future__ import annotations


class InterviewAssistantError(Exception):
    """所有业务异常的基类。"""


class SessionError(InterviewAssistantError):
    """面试会话生命周期相关的错误（状态机非法转换、未找到会话等）。"""


class LLMTimeoutError(InterviewAssistantError):
    """LLM 调用超时。"""


class LLMRateLimitError(InterviewAssistantError):
    """LLM 触发限流（HTTP 429 等）。"""


class LLMResponseError(InterviewAssistantError):
    """LLM 返回非预期结果（解析失败、内容缺失等）。"""


class StorageError(InterviewAssistantError):
    """持久化层错误（数据库未初始化、约束冲突等）。"""


class AudioError(InterviewAssistantError):
    """音频采集 / 录制相关错误。"""


class STTError(InterviewAssistantError):
    """语音识别（STT）相关错误。"""