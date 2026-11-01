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

# 会带出 aweme 对象的接口。detail 是最全的那个；另外两个在 detail 没响应时捞一把。
_AWEME_API = ("/aweme/v1/web/aweme/detail/",
              "/aweme/v1/web/aweme/post/",
              "/aweme/v1/web/aweme/related/")

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

def _capture(video_id: str, timeout_s: float = 30.0) -> tuple[dict, dict[str, str], dict]:
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
                if not detail and any(k in u for k in _AWEME_API):
                    ad = _pick_aweme(resp.json(), video_id)
                    if ad:
                        detail.update(ad)
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

        def wait_for(pg, budget: float) -> None:
            deadline = time.time() + budget
            while time.time() < deadline:
                muxed = "/video/tos/" in streams.get("video", "")
                if "video" in streams and detail and (muxed or "audio" in streams):
                    return
                pg.wait_for_timeout(400)

        wait_for(pg, timeout_s)
        if not detail:
            # detail 接口没响，可能是页面复用了前端缓存没重放请求 —— 整页重载一次再等
            try:
                pg.reload(wait_until="domcontentloaded", timeout=60000)
                wait_for(pg, 12.0)
            except Exception:      # noqa: BLE001 —— 重载失败就用已有的东西
                pass
        pg.wait_for_timeout(1200)      # 给 detail 接口收尾

        dom = _meta_from_dom(pg)       # 兜底：接口缺什么，DOM 补什么
        b.close()

    return detail, streams, dom


def _pick_aweme(j, video_id: str) -> dict | None:
    """从一个接口响应里，捞出**就是这条视频**的 aweme 对象；捞不到返回 None。

    按 aweme_id 校验过 —— post/related 这类接口里装的是别人的视频，
    不校验就会把隔壁的标题作者当成本条的。
    """
    if not isinstance(j, dict):
        return None
    ad = j.get("aweme_detail")
    if isinstance(ad, dict):
        return ad if str(ad.get("aweme_id")) == video_id else None
    for key in ("aweme_list", "data"):
        v = j.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict) and str(x.get("aweme_id")) == video_id:
                    return x
    return None


def _meta_from_dom(pg) -> dict:
    """兜底：detail 接口缺字段时，从页面 DOM 补。"""
    out = {}
    for key, sels in (
        ("desc", ('[data-e2e="video-desc"]', '[data-e2e="video-desc-content"]', "h1")),
        ("author", ('[data-e2e="video-author-name"]', '[data-e2e="user-name"]',
                    '.account-name')),
    ):
        for sel in sels:
            try:
                v = pg.inner_text(sel, timeout=1500).strip()
                if v:
                    out[key] = v
                    break
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

    # ---- 元数据：接口优先。
    # DOM 兜底有坑：播放页会轮播"下一条"，页面上写着的可能根本不是这个 id 的视频。
    # 所以只有「DOM 里那段文案和接口里的一致」才敢用 DOM 补字段。
    desc_api = detail.get("desc") or ""
    desc_dom = dom.get("desc") or ""
    same_video = bool(desc_api) and (not desc_dom or desc_dom[:15] == desc_api[:15])

    if not detail:
        notes.append("detail 接口没拦到，元数据取自 DOM；页面可能轮播到别的视频，请核对标题")

    item.title = desc_api or desc_dom
    item.author = (detail.get("author") or {}).get("nickname") or ""
    if not item.author and same_video:
        item.author = dom.get("author", "")
    dur_ms = detail.get("duration") or (detail.get("video") or {}).get("duration")
    item.duration = round(dur_ms / 1000, 1) if dur_ms else None
    ct = detail.get("create_time")
    if ct:
        item.publish_time = datetime.fromtimestamp(ct, CST).strftime("%Y-%m-%d %H:%M")
    st = detail.get("statistics") or {}
    item.stats = Stats(like=st.get("digg_count"), comment=st.get("comment_count"),
                       collect=st.get("collect_count"), share=st.get("share_count"))
    item.raw = {"aweme_id": detail.get("aweme_id") or vid}

    if not item.author:
        notes.append("作者名没拿到（接口和 DOM 都空）")
    if item.stats.like is None:
        notes.append("互动数没拿到（这条视频页没返回 statistics）")

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
