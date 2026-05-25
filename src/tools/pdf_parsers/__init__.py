"""PDF 解析器包，通过 Strategy 模式支持多种解析引擎。"""
from .base import BasePDFParser
from .pymupdf_parser import PymupdfParser
from .qwen_vl_parser import QwenVLParser
from .mineru_parser import MineruParser

__all__ = ["BasePDFParser", "PymupdfParser", "QwenVLParser", "MineruParser"]
