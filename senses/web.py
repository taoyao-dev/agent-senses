"""网页搜索与抓取 —— 接 DeepSeek 的 Search / Fetch API。

两个端点（和 Chat API 同一把 key，独立计费）：

    POST https://api.deepseek.com/v1/tools/search   {"query": ..., "count": 10}
    POST https://api.deepseek.com/v1/tools/fetch    {"url": ...}

计费：按成功请求（HTTP 200 且有结果/正文）算，各 5 元 / 1000 次。
2026-11-01 前免费。

跟 douyin.py 的区别：那边是"进得去一个平台"，这边是"看得见整个互联网"。
共同点：都是给 agent 的器官，返回结构化数据，不返回裸 HTML。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx
import yaml

BASE = "https://api.deepseek.com"
SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"

# 408 / 429 / 5xx 可以重试，其他 4xx 重试也没用
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class WebError(RuntimeError):
    """带 HTTP 状态码的失败，方便上层判断要不要重试。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Hit:
    """一条搜索结果。summary 是给模型读的（600-800 字），snippet 是给人看的摘要。"""
    title: str = ""
    url: str = ""
    snippet: str = ""
    summary: str = ""
    content: str = ""          # 只有 need_content=True 才有
    date: str = ""
    rerank_score: float | None = None
    authority: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Page:
    """抓下来的一个网页。content 是 Markdown。"""
    url: str
    title: str = ""
    content: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- key

def _api_key() -> str:
    """按优先级找 key：环境变量 → Claude Code settings → DSH 凭据库。

    三条路都只取到值为止，绝不打印、不落日志。
    """
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        v = os.environ.get(name)
        if v and v.strip():
            return v.strip()

    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            env = json.loads(settings.read_text(encoding="utf-8")).get("env", {})
            for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
                v = env.get(name)
                if v and str(v).strip():
                    return str(v).strip()
        except (OSError, ValueError):
            pass

    creds = Path.home() / ".dsh" / ".credentials.yaml"
    if creds.exists():
        try:
            refs = (yaml.safe_load(creds.read_text(encoding="utf-8")) or {}).get("refs", {})
            v = refs.get("DEEPSEEK_API_KEY")
            if v and str(v).strip():
                return str(v).strip()
        except (OSError, yaml.YAMLError):
            pass

    raise WebError(
        "找不到 DeepSeek API key：设环境变量 DEEPSEEK_API_KEY，"
        "或在 ~/.claude/settings.json 的 env 里配 ANTHROPIC_AUTH_TOKEN"
    )


# ---------------------------------------------------------------- HTTP

def _post(path: str, payload: dict, timeout: float = 60.0) -> dict:
    """POST 一次，按状态码决定重不重试。"""
    key = _api_key()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last: Exception | None = None

    for attempt in range(3):
        try:
            r = httpx.post(BASE + path, json=payload, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:          # 连接层错误，退避重试
            last = WebError(f"网络错误：{type(e).__name__}: {e}")
            time.sleep(0.8 * (attempt + 1))
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except ValueError as e:
                raise WebError(f"返回的不是 JSON：{e}", r.status_code) from e

        body = r.text[:300]
        err = WebError(f"HTTP {r.status_code}: {body}", r.status_code)
        if r.status_code not in RETRY_STATUS:
            raise err                    # 4xx（400/401/402/403/404/413）别重试
        last = err
        time.sleep(0.8 * (attempt + 1))

    assert last is not None
    raise last


# ---------------------------------------------------------------- 对外

def search(query: str, count: int = 10, need_content: bool = False,
           ori_question: str | None = None, timeout: float = 60.0) -> list[Hit]:
    """搜网页。

    query 支持操作符：site: example.com / -site: / "精确短语" / -"排除" / lang:zh,en /
    after:2025 / before:2026。count 上限 50。
    """
    if not query or not query.strip():
        raise WebError("query 不能为空")
    payload: dict[str, Any] = {"query": query.strip(), "count": max(1, min(50, int(count)))}
    if need_content:
        payload["need_content"] = True
    if ori_question:
        payload["ori_question"] = ori_question[:128]

    data = _post(SEARCH_PATH, payload, timeout)
    hits: list[Hit] = []
    for it in data.get("results") or []:
        if not isinstance(it, dict):
            continue
        hits.append(Hit(
            title=str(it.get("title") or ""),
            url=str(it.get("url") or ""),
            snippet=str(it.get("snippet") or ""),
            summary=str(it.get("summary") or ""),
            content=str(it.get("content") or ""),
            date=str(it.get("date") or ""),
            rerank_score=it.get("rerank_score"),
            authority=it.get("authority"),
        ))
    return hits


def fetch(url: str, timeout: float = 60.0) -> Page:
    """抓一个网页，正文转成 Markdown。"""
    if not url or not url.strip():
        raise WebError("url 不能为空")
    data = _post(FETCH_PATH, {"url": url.strip()}, timeout)
    return Page(
        url=url.strip(),
        title=str(data.get("title") or ""),
        content=str(data.get("content") or ""),
    )
