"""语音转写。

两个坑这里都填掉了：
1. faster-whisper 1.x 调 av.open(metadata_errors=...) 和本地 PyAV 版本对不上 → 不走它的解码，
   自己用 wave 读成 numpy 数组喂进去。
2. HF cache 里的模型可能是"半个"（config 有、model.bin 没下完）→ 先验完整性再加载。
"""
from __future__ import annotations

import os
import time
import wave
from pathlib import Path

import numpy as np

from .models import Segment

DEFAULT_MODEL = "small"
_ENV_DIR = "SENSES_WHISPER_DIR"

# whisper 的中文输出会在简繁之间来回跳（同一条音频里"吃點東西"和"宝头强"并存），
# initial_prompt 压不住 —— 实测反而让别的词念错。只能事后转一遍。
try:
    from opencc import OpenCC
    _S2T = OpenCC("t2s")
except Exception:            # 没装就原样返回，不阻断转写
    _S2T = None


def to_simplified(text: str) -> str:
    """繁体转简体。没装 opencc 就原样返回。"""
    return _S2T.convert(text) if _S2T else text


def read_wav_mono16k(path: str | Path) -> np.ndarray:
    """读 16k 单声道 wav 成 float32 数组。"""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"需要 16bit 单声道 wav，实际 {w.getnchannels()}ch/"
                             f"{w.getsampwidth()*8}bit")
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000:
        raise ValueError(f"需要 16000Hz，实际 {sr}Hz（用 media.extract_audio 转）")
    return audio


def find_model(name: str = DEFAULT_MODEL) -> str:
    """定位一个完整的模型目录。

    顺序：环境变量 → HF cache 里第一个真正下完的 snapshot。
    """
    env = os.environ.get(_ENV_DIR)
    if env and Path(env).exists():
        return env

    hub = Path.home() / ".cache" / "huggingface" / "hub" / f"models--Systran--faster-whisper-{name}"
    snaps = sorted((hub / "snapshots").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for s in snaps:
        if (s / "model.bin").exists():
            return str(s)

    raise FileNotFoundError(
        f"找不到完整的 faster-whisper-{name} 模型。"
        f"下模型：huggingface-cli download Systran/faster-whisper-{name} "
        f"（国内加 HF_ENDPOINT=https://hf-mirror.com），"
        f"或用 {_ENV_DIR} 指定目录。"
    )


def transcribe(wav: str | Path, *, model: str = DEFAULT_MODEL,
               language: str = "zh", beam_size: int = 5) -> list[Segment]:
    """转写。先试 GPU，不行退 CPU——GPU 缺 cublas64_12.dll 是这台机的常态。"""
    from faster_whisper import WhisperModel

    audio = read_wav_mono16k(wav)
    path = find_model(model)
    last: Exception | None = None

    for dev, ct in (("cuda", "float16"), ("cpu", "int8")):
        try:
            m = WhisperModel(path, device=dev, compute_type=ct)
            segs, _ = m.transcribe(audio, language=language, beam_size=beam_size,
                                   condition_on_previous_text=False)
            return [Segment(t=round(s.start, 1), text=to_simplified(s.text.strip()))
                    for s in segs]
        except Exception as e:      # noqa: BLE001 —— 任何加载/推理失败都降级
            last = e
            continue
    raise RuntimeError(f"转写失败（GPU/CPU 都试过）：{last}")
