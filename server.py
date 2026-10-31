"""agent-senses 的 MCP 外壳。

跑法：
    python server.py                 # stdio，给 Claude Code / Cursor 用
    python server.py --selftest URL  # 不走 MCP，直接跑一遍看结果

注意：stdio 传输下 stdout 是协议通道，任何 print 都会把协议搞坏。
      —— 所有输出走 stderr，工具结果走返回值。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent))

from senses import douyin, web  # noqa: E402

CACHE = Path(__file__).parent / "_cache"
mcp = FastMCP("agent-senses")


@mcp.tool()
async def douyin_get(url: str, transcript: bool = True,
                     keep_video: bool = False) -> str:
    """读一条抖音视频：返回文案、作者、时长、发布时间、互动数据，以及逐句转写。

    参数：
      url         抖音链接。短链（v.douyin.com/xxx）和完整链都行。
      transcript  是否做语音转写（默认 True）。关掉更快，只拿元数据。
      keep_video  是否保留下载的成片（默认 False，只留音频和转写文本）。

    返回 JSON 字符串。字段：
      title/author/duration/publish_time/stats —— 基本信息
      text        —— 转写全文（转录后的口播稿，可能有错别字，是"速记"不是"精校"）
      transcript  —— 逐句，带起始秒 t
      media       —— 本地文件路径
      notes       —— 降级说明（哪一步没走通），空表示全绿

    注意：内容平台有风控，单次约 30-60 秒。转写稿按口音和语速会有错字，引用前自行核对。
    """
    def work():
        return douyin.fetch(url, workdir=CACHE / "douyin",
                            want_video=keep_video, want_transcript=transcript)

    try:
        item = await anyio.to_thread.run_sync(work)
    except Exception as e:      # noqa: BLE001 —— 错误也要结构化返回给 agent
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                          ensure_ascii=False, indent=2)

    d = item.to_dict()
    d["ok"] = True
    if not transcript:
        d.pop("transcript", None)
    return json.dumps(d, ensure_ascii=False, indent=2)


@mcp.tool()
async def ds_search(query: str, count: int = 10,
                    content: bool = False) -> str:
    """搜网页（DeepSeek Search API）。比内置搜索更适合中文内容和批量调研。

    参数：
      query    搜索词。支持操作符：
                 site:example.com / -site:example.com —— 限定或排除站点
                 "精确短语" / -"排除短语"
                 lang:zh,en      语言
                 after:2025 / before:2026   按年份过滤
      count    返回条数，1-50，默认 10。
      content  是否带正文全文（默认 False）。开着更慢也更占上下文。

    返回 JSON 字符串，results[] 每项：
      title / url / snippet(100-200字摘要) / summary(600-800字，给模型读)
      date / rerank_score(0-3 越高越靠前) / authority(-1~4 权威度)
      content(仅 content=True 时非空)

    计费：按成功请求 5 元/千次，2026-11-01 前免费。
    """
    def work():
        return web.search(query, count=count, need_content=content)

    try:
        hits = await anyio.to_thread.run_sync(work)
    except Exception as e:      # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                          ensure_ascii=False, indent=2)

    return json.dumps({"ok": True, "count": len(hits),
                       "results": [h.to_dict() for h in hits]},
                      ensure_ascii=False, indent=2)


@mcp.tool()
async def ds_fetch(url: str, max_chars: int = 30000) -> str:
    """抓一个网页，正文转成 Markdown（DeepSeek Fetch API）。

    参数：
      url        完整 URL，必须以 http:// 或 https:// 开头，不能带用户名密码。
      max_chars  正文最多返回多少字符，默认 30000。超了会截断并在 notes 里说明。

    返回 JSON 字符串：url / title / content(Markdown) / notes / truncated。

    注意：抓取受目标站点可用性和访问策略影响，403 表示被安全策略拒绝，
    不代表 URL 错了。计费和搜索一样，按成功请求 5 元/千次，2026-11-01 前免费。
    """
    def work():
        return web.fetch(url)

    try:
        page = await anyio.to_thread.run_sync(work)
    except Exception as e:      # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                          ensure_ascii=False, indent=2)

    truncated = len(page.content) > max_chars
    if truncated:
        page.notes.append(f"正文 {len(page.content)} 字符，已截断到 {max_chars}")
        page.content = page.content[:max_chars]

    d = page.to_dict()
    d.update({"ok": True, "truncated": truncated})
    return json.dumps(d, ensure_ascii=False, indent=2)


def _selftest(url: str) -> None:
    """绕开 MCP 直接跑一遍，调试用。"""
    item = douyin.fetch(url, workdir=CACHE / "douyin")
    print(f"标题: {item.title}", file=sys.stderr)
    print(f"作者: {item.author}  时长: {item.duration}s  发布: {item.publish_time}",
          file=sys.stderr)
    print(f"数据: {item.stats}", file=sys.stderr)
    print(f"转写 {len(item.transcript)} 句 / {len(item.text)} 字", file=sys.stderr)
    print(f"文件: {item.media}", file=sys.stderr)
    print(f"备注: {item.notes}", file=sys.stderr)
    print("---- 前 300 字 ----", file=sys.stderr)
    print(item.text[:300], file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        _selftest(sys.argv[2])
    else:
        mcp.run()
