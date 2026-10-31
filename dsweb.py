"""DeepSeek Search / Fetch 的命令行入口。

    python dsweb.py search "查询词" --count 5 [--content]
    python dsweb.py fetch "https://..." [--max-chars 30000]

输出默认是 JSON（--pretty 缩进）。给脚本用就别加 --pretty。

为什么单独做一个 CLI：MCP 要挂在会话里，脚本和定时任务挂不上。
这个入口不依赖任何协议，管道、cron、流水线里都能直接调。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from senses import web  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="dsweb", description="DeepSeek Search / Fetch")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="搜网页")
    s.add_argument("query")
    s.add_argument("--count", type=int, default=10)
    s.add_argument("--content", action="store_true", help="带上正文全文")
    s.add_argument("--ori", help="原始问题，帮助意图理解（≤128 字）")
    s.add_argument("--pretty", action="store_true", help="缩进输出")

    f = sub.add_parser("fetch", help="抓网页")
    f.add_argument("url")
    f.add_argument("--max-chars", type=int, default=30000)
    f.add_argument("--pretty", action="store_true", help="缩进输出")

    args = ap.parse_args()

    indent = 2 if args.pretty else None
    try:
        if args.cmd == "search":
            hits = web.search(args.query, count=args.count,
                              need_content=args.content, ori_question=args.ori)
            out = {"ok": True, "count": len(hits),
                   "results": [h.to_dict() for h in hits]}
        else:
            page = web.fetch(args.url)
            truncated = len(page.content) > args.max_chars
            if truncated:
                page.notes.append(
                    f"正文 {len(page.content)} 字符，已截断到 {args.max_chars}")
                page.content = page.content[:args.max_chars]
            out = page.to_dict()
            out.update({"ok": True, "truncated": truncated})
    except Exception as e:      # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                         ensure_ascii=False, indent=indent))
        return 1

    print(json.dumps(out, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
