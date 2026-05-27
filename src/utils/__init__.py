"""通用工具：原子写入、容错数值解析、Metrics 单例等。"""
from .atomic_io import write_atomic
from .metrics import Metrics
from .numeric import safe_float

__all__ = ["Metrics", "safe_float", "write_atomic"]
