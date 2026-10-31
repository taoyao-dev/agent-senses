"""抖音接入。

链路（每一步都是被 Argus 风控逼出来的）：
    短链 ──302──> video_id ──Playwright(系统Chrome)──> 媒体分片URL + detail接口JSON
                                                          │
                                              带 Referer 下载 ──> ffmpeg 合流 ──> ASR

要点：
- yt-dlp 拿不到（要新鲜 cookie）；浏览器 cookie 也读不出来（Chromium App-Bound 加密）
- 所以用 playwright 驱动**系统里已装的 Chrome**（channel="chrome"，不用下 chromium）
- 分片 URL 有时效签名，**抓到手必须立刻下**
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from . import asr as asr_mod
from . import media as media_mod
from .models import Item, Stats

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://www.douyin.com/"
_ID_RE = re.compile(r"(?:/video/|/share/video/|modal_id=|/note/)(\d{6,})")

CST = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 解析短链

def resolve(url: str) -> tuple[str, str]:
    """短链 → (video_id, 规范URL)。已经是规范链就直接切。"""
    m = _ID_RE.search(url)
    if m:
        vid = m.group(1)
        return vid, f"https://www.douyin.com/video/{vid}"

    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=False, timeout=20)
    loc = r.headers.get("location", "")
    m = _ID_RE.search(loc)
    if not m:
        raise ValueError(f"解析不出 video_id：{url!r} → location={loc!r}")
    vid = m.group(1)
    return vid, f"https://www.douyin.com/video/{vid}"


# ---------------------------------------------------------------- 抓取

def _capture(video_id: str, timeout_s: float = 30.0) -> tuple[dict, dict[str, str]]:
    """开一个有头绪的浏览器，同时收「元数据」和「媒体分片地址」。"""
    from playwright.sync_api import sync_playwright

    detail: dict = {}
    streams: dict[str, str] = {}

    with sync_playwright() as p:
        b = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--mute-audio"],
        )
        ctx = b.new_context(user_agent=UA, viewport={"width": 1280, "height": 900},
                            locale="zh-CN")
        pg = ctx.new_page()

        def on_response(resp):
            u = resp.url
            try:
                if "/aweme/v1/web/aweme/detail/" in u and not detail:
                    j = resp.json()
                    if j.get("aweme_detail"):
                        detail.update(j["aweme_detail"])
                elif "douyinvod.com" in u or "douyin" in u:
                    # ★ 新版播放器直接给合成好的 mp4（路径 /video/tos/...，自带音轨），
                    #   旧版才是分开的 /media-video 和 /media-audio 两条。
                    if "/video/tos/" in u and "video" not in streams:
                        streams["video"] = u
                    elif "/media-video" in u and "video" not in streams:
                        streams["video"] = u
                    elif "/media-audio" in u and "audio" not in streams:
                        streams["audio"] = u
            except Exception:      # 非 JSON / 已关闭的响应，忽略
                pass

        pg.on("response", on_response)
        pg.goto(f"https://www.douyin.com/video/{video_id}",
                wait_until="domcontentloaded", timeout=60000)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            muxed = "/video/tos/" in streams.get("video", "")
            if "video" in streams and detail and (muxed or "audio" in streams):
                break
            pg.wait_for_timeout(400)
        pg.wait_for_timeout(1200)      # 给 detail 接口收尾

        dom = _meta_from_dom(pg)       # 接口没拦到时的兜底，趁页面还开着抠
        b.close()

    return detail, streams, dom


def _meta_from_dom(pg) -> dict:
    """兜底：detail 接口没拦到时，从页面 DOM 抠。"""
    out = {}
    for key, sel in [
        ("desc", '[data-e2e="video-desc"]'),
        ("author", '[data-e2e="video-author-name"]'),
    ]:
        try:
            v = pg.inner_text(sel, timeout=2000).strip()
            if v:
                out[key] = v
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- 下载

def _download(url: str, out: Path) -> Path:
    headers = {"User-Agent": UA, "Referer": REFERER}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                      timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
    return out


# ---------------------------------------------------------------- 主入口

def fetch(url: str, *, workdir: str | Path | None = None,
          want_video: bool = True, want_transcript: bool = True,
          model: str = asr_mod.DEFAULT_MODEL) -> Item:
    """给一个抖音链接，还一条结构化内容。"""
    vid, canon = resolve(url)
    notes: list[str] = []

    detail, streams, dom = _capture(vid)
    if not streams:
        raise RuntimeError(f"没抓到媒体流（{vid}）——页面结构可能变了，或风控拦了")

    item = Item(platform="douyin", id=vid, url=canon)

    # ---- 元数据
    if detail:
        item.title = detail.get("desc", "")
        item.author = (detail.get("author") or {}).get("nickname", "")
        dur_ms = detail.get("duration") or (detail.get("video") or {}).get("duration")
        item.duration = round(dur_ms / 1000, 1) if dur_ms else None
        ct = detail.get("create_time")
        if ct:
            item.publish_time = datetime.fromtimestamp(ct, CST).strftime("%Y-%m-%d %H:%M")
        st = detail.get("statistics") or {}
        item.stats = Stats(like=st.get("digg_count"), comment=st.get("comment_count"),
                           collect=st.get("collect_count"), share=st.get("share_count"))
        item.raw = {"aweme_id": detail.get("aweme_id")}
    else:
        notes.append("detail 接口没拦到，元数据降级自 DOM")
        item.title = dom.get("desc", "")
        item.author = dom.get("author", "")

    # ---- 落盘
    wd = Path(workdir) if workdir else Path.cwd() / "_cache" / vid
    wd.mkdir(parents=True, exist_ok=True)

    muxed = "/video/tos/" in streams["video"]
    vpath = _download(streams["video"], wd / "video.mp4")
    apath = vpath if muxed else _download(streams["audio"], wd / "audio.m4a")

    # ---- 转写
    if want_transcript:
        try:
            wav = media_mod.extract_audio(apath, wd / "asr16k.wav")
            item.transcript = asr_mod.transcribe(wav, model=model)
            item.text = "".join(s.text for s in item.transcript)
        except Exception as e:      # noqa: BLE001 —— 转写挂了不该毁掉整条结果
            notes.append(f"转写失败：{type(e).__name__}: {e}")

    # ---- 合流（要留视频才做，费时）
    if want_video:
        try:
            # 分片合流的老路；新版拿到的本来就是成品 mp4，不用再合
            full = vpath if muxed else media_mod.merge(vpath, apath, wd / "full.mp4")
            item.media["video"] = str(full)
            if item.duration is None:
                item.duration = media_mod.duration(full)
        except Exception as e:      # noqa: BLE001
            notes.append(f"合流失败：{e}")
            item.media["video"], item.media["audio"] = str(vpath), str(apath)
    else:
        vpath.unlink(missing_ok=True)
        item.media["audio"] = str(apath)

    item.notes = notes
    return item
