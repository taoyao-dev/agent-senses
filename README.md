# agent-senses

**给 agent 用的感官。** ｜ [English](README.en.md)

大模型不缺脑子，缺的是"看见"和"进去"的能力。一个中文内容链接，人点开就看，agent 却要
撞反爬、绕签名、下流、转写——十几轮工具调用。

这个项目把那十几轮压成**一次函数调用**。

```
douyin_get(url)  →  文案 + 作者 + 时长 + 互动数据 + 逐句转写 + 本地文件
ds_search(query) →  标题 + URL + 摘要 + 长 summary + 发布日期 + 权威度
ds_fetch(url)    →  网页正文（Markdown）
```

## 装

```bash
pip install -r requirements.txt
# 用系统 Chrome 驱动，不用下 chromium；确认本机装了 Chrome
```

ffmpeg 不装也行，会退到 `imageio-ffmpeg` 自带那份。
转写模型默认 `small`，从 HF cache 找；下模型：

```bash
HF_ENDPOINT=https://hf-mirror.com huggingface-cli download Systran/faster-whisper-small
# 或用环境变量 SENSES_WHISPER_DIR 直接指定模型目录
```

## 用

**直接跑（调试）**

```bash
python server.py --selftest "https://v.douyin.com/xxxx/"
```

**当 MCP server 挂进 Claude Code**

```bash
claude mcp add agent-senses --scope user -- <python路径> "<本目录>/server.py"
# 撤销：claude mcp remove agent-senses
```

**搜索 / 抓网页（DeepSeek Search & Fetch API）**

不依赖 MCP 会话，脚本、管道、定时任务都能直接调：

```bash
python dsweb.py search "番茄短故事 千字万金 分成" --count 5 --pretty
python dsweb.py fetch "https://www.ithome.com/0/101/4328.htm" --max-chars 5000
```

`query` 支持 `site:` / `-site:` / `"精确短语"` / `lang:zh,en` / `after:2025` 等操作符。
key 按 `DEEPSEEK_API_KEY` → `~/.claude/settings.json` → `~/.dsh/.credentials.yaml` 的顺序找。

计费：各 5 元 / 1000 次成功请求，**2026-11-01 前免费**。

**当库用**

```python
from senses import douyin
item = douyin.fetch("https://v.douyin.com/xxxx/", want_video=False)
print(item.author, item.duration)
print(item.text)
```

## 架构

```
senses/            核心库 —— 能力的本体，跟 MCP 无关
  models.py        返回给 agent 的数据契约
  douyin.py        抖音接入（解析 → 抓取 → 下载 → 合流 → 转写）
  web.py           网页搜索/抓取（DeepSeek Search & Fetch API）
  media.py         ffmpeg 封装
  asr.py           语音转写
server.py          MCP 外壳 —— 薄，随时可换 CLI / HTTP
dsweb.py           搜索/抓取的 CLI 入口 —— 给不挂 MCP 的脚本用
tests/             协议层自测
```

**为什么库和外壳分开**：MCP 协议还在变。核心能力做成独立库，协议换了只换外壳，
本体重写不了。

## 加下一个平台

照抄 `douyin.py` 的形状：

1. `resolve(url)` —— 链接归一化，拿到平台内部 id
2. `_capture(id)` —— 把页面数据抠出来（**优先拦接口 JSON，别抠 DOM 文本**）
3. `_download / merge / transcribe` —— 这三步是共用的，直接 import
4. 在 `server.py` 注册一个 `xxx_get` 工具

返回统一用 `Item`，agent 看到的形状就不变。

## 踩过的坑（别重踩）

| 现象 | 原因 | 解法 |
|---|---|---|
| yt-dlp 报 `Fresh cookies needed` | 抖音 Argus 风控 | 不走 yt-dlp |
| `--cookies-from-browser` 报 DPAPI | Chromium App-Bound 加密 | 不走浏览器 cookie |
| 分享页 `_ROUTER_DATA` 是空的 | SSR 已不下发详情 | 走 Playwright 跑真页面 |
| 直连 detail 接口 403 | `Uifid Not Found` | 拦页面自己发的请求 |
| 分片下载 403 | 缺 Referer | 带 `Referer: https://www.douyin.com/` |
| `av.open(metadata_errors=)` TypeError | PyAV 版本对不上 | 用 wave+numpy 直喂，绕开解码 |
| 模型加载报 snapshot 不完整 | `model.bin` 没下完 | 加载前验 `model.bin` 存在 |
| GPU 报 `cublas64_12.dll not found` | 缺 cuBLAS | 自动退 CPU int8 |

## 边界（当前版本明确不做）

- 不碰登录态、不做账号操作、不抓私密内容
- 只读公开内容
- 转写是"速记"不是"精校"——口音/语速会造成错字，引用前核对
- 单平台、单条。批量、去重、缓存都还没有
