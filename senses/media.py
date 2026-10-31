"""ffmpeg 封装。

ffmpeg 不保证在 PATH 上——优先用 imageio-ffmpeg 自带的那份（见记忆 ffmpeg-location）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def ffmpeg_exe() -> str:
    """找一个能用的 ffmpeg。找不到就抛。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        raise RuntimeError("找不到 ffmpeg：PATH 上没有，imageio-ffmpeg 也没装") from e


def _run(args: list[str]) -> None:
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{p.stderr.strip()[:500]}")


def merge(video: str | Path, audio: str | Path, out: str | Path) -> Path:
    """把分离的音视频流合流。stream copy，不重编。"""
    out = Path(out)
    _run(["-y", "-i", str(video), "-i", str(audio), "-c", "copy", str(out)])
    return out


def extract_audio(src: str | Path, out: str | Path, sr: int = 16000) -> Path:
    """抽成 whisper 要的 16k 单声道 wav。"""
    out = Path(out)
    _run(["-y", "-i", str(src), "-vn", "-ar", str(sr), "-ac", "1", str(out)])
    return out


def duration(src: str | Path) -> float | None:
    """读时长（秒）。失败返回 None，不阻断主流程。"""
    try:
        p = subprocess.run([ffmpeg_exe(), "-i", str(src)],
                           capture_output=True, text=True)
        for line in p.stderr.splitlines():
            if "Duration:" in line:
                hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
                return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        pass
    return None


def extract_frames(src: str | Path, times: list[float], outdir: str | Path,
                   width: int = 540) -> list[Path]:
    """按时间点抽帧，给 agent 看画面用。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for t in times:
        p = outdir / f"f_{t:g}.jpg"
        try:
            _run(["-y", "-ss", str(t), "-i", str(src), "-frames:v", "1",
                  "-vf", f"scale={width}:-1", str(p)])
            if p.exists():
                out.append(p)
        except RuntimeError:
            continue
    return out
