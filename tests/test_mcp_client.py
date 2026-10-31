"""用 MCP 协议连一次自己的 server，确认外壳真的能用。

    python tests/test_mcp_client.py [抖音链接]

不传链接就只做握手 + list_tools。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main(url: str | None) -> int:
    params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print(f"[+] 握手成功，工具：{names}")
            if "douyin_get" not in names:
                print("[-] 没注册上 douyin_get")
                return 1

            if not url:
                return 0

            print(f"[+] 调用 douyin_get({url}) …")
            res = await s.call_tool("douyin_get",
                                    {"url": url, "transcript": False, "keep_video": False})
            payload = json.loads(res.content[0].text)
            if not payload.get("ok"):
                print(f"[-] 工具报错：{payload.get('error')}")
                return 1
            print(f"[+] ok  title={payload['title'][:40]}")
            print(f"[+]      author={payload['author']}  dur={payload['duration']}s")
            print(f"[+]      stats={payload['stats']}")
            print(f"[+]      notes={payload['notes']}")
            return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(asyncio.run(main(arg)))
