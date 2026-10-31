"""agent-senses —— 给 agent 用的「感官」。

核心能力做成独立库，MCP / CLI / HTTP 都只是外壳。
"""
from .models import Item, Segment, Stats

__all__ = ["Item", "Segment", "Stats"]
__version__ = "0.1.0"
