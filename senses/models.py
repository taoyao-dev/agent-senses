"""返回给 agent 的数据结构。

这一层是「契约」：平台换来换去，agent 看到的形状不变。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Segment:
    """一句转写。t=起始秒，text=文本。"""
    t: float
    text: str


@dataclass
class Stats:
    like: int | None = None
    comment: int | None = None
    collect: int | None = None
    share: int | None = None


@dataclass
class Item:
    """一条内容的完整结构化表示。"""
    platform: str
    id: str
    url: str
    title: str = ""            # 文案（抖音里就是 desc）
    author: str = ""
    duration: float | None = None
    publish_time: str = ""
    stats: Stats = field(default_factory=Stats)
    transcript: list[Segment] = field(default_factory=list)
    text: str = ""             # transcript 拼成的纯文本，方便直接读
    media: dict[str, str] = field(default_factory=dict)   # {"video": path, "audio": path}
    notes: list[str] = field(default_factory=list)        # 降级/失败的说明
    raw: dict[str, Any] = field(default_factory=dict)     # 平台原始字段，排查用

    def to_dict(self) -> dict:
        return asdict(self)
