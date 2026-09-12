import hashlib
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe

import subtitle_plugin


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".ts", ".m4v", ".webm", ".flv", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class MediaError(Exception):
    pass


class CancelledError(Exception):
    pass


def _ffmpeg() -> str:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / "bin" / "ffmpeg.exe")
    candidates.append(Path(__file__).resolve().parent / "bin" / "ffmpeg.exe")
    try:
        candidates.append(Path(get_ffmpeg_exe()))
    except Exception:
        pass
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise MediaError("找不到 FFmpeg。")


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def cache_dir() -> Path:
    path = _app_root() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_fingerprint(path: str, extra: str = "") -> str:
    st = os.stat(path)
    payload = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{extra}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _norm_cache_path(src: str, width: int, height: int, has_audio: bool, normalize_audio: bool) -> Path:
    # normv2：音频处理加入 aresample=async=1:first_pts=0 强制音画对齐，旧缓存（normv1）作废
    key = _file_fingerprint(src, f"normv2|{width}x{height}|{has_audio}|{normalize_audio}")
    folder = cache_dir() / "norm"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.mp4"


def _thumb_cache_path(src: str, max_width: int) -> Path:
    key = _file_fingerprint(src, f"thumb|{max_width}")
    folder = cache_dir() / "thumbs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.jpg"


def scan_videos(folder: str) -> list[str]:
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    items = sorted(
        [str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
        key=lambda x: Path(x).name.lower(),
    )
    return items


def scan_audio(folder: str) -> list[str]:
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    items = sorted(
        [str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS],
        key=lambda x: Path(x).name.lower(),
    )
    return items


def parse_duration(value: str) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", value)
    if not match:
        return 0.0
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def probe_media(path: str) -> dict:
    cmd = [
        _ffmpeg(),
        "-hide_banner",
        "-i",
        path,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    text = proc.stderr or ""
    duration = parse_duration(text)
    has_audio = bool(re.search(r"Stream #\d+:\d+[^\n]*Audio:", text))
    has_video = bool(re.search(r"Stream #\d+:\d+[^\n]*Video:", text))
    width = height = 0
    vm = re.search(r"Video:\s*\S+.*?(\d{2,5})x(\d{2,5})", text)
    if vm:
        width, height = int(vm.group(1)), int(vm.group(2))
    audio_start = 0.0
    audio_duration = 0.0
    am = re.search(
        r"Stream #\d+:\d+[^\n]*Audio:(?:(?!Stream #)[\s\S])*?Start:\s*([\d.]+)[^\n]*Duration:\s*([\d.]+)",
        text,
    )
    if am:
        audio_start = float(am.group(1))
        audio_duration = float(am.group(2))
    return {
        "duration": duration or 1.0,
        "has_audio": has_audio,
        "has_video": has_video,
        "width": width,
        "height": height,
        "audio_start": audio_start,
        "audio_duration": audio_duration,
        "ok": bool(has_video and duration > 0.05),
    }


def escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def run_ffmpeg(
    args: list[str],
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if log:
        log(" ".join(str(a) for a in args))
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        while proc.poll() is None:
            if cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise CancelledError("任务已取消")
            while pause_event.is_set() and not cancel_event.is_set():
                time.sleep(0.2)
            time.sleep(0.1)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    if proc.returncode != 0:
        raise MediaError(f"FFmpeg 执行失败，返回码 {proc.returncode}")


def _filter_scale_pad(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps=30,format=yuv420p"
    )


def normalize_clip(
    src: str,
    dst: str,
    width: int,
    height: int,
    duration: float,
    has_audio: bool,
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
    normalize_audio: bool = False,
) -> None:
    cached = _norm_cache_path(src, width, height, has_audio, normalize_audio)
    if cached.exists() and cached.stat().st_size > 0:
        shutil.copy2(cached, dst)
        if log:
            log(f"命中归一化缓存：{Path(src).name}")
        return

    tmp_cache = cached.with_name(
        f"{cached.stem}.{os.getpid()}.{threading.get_ident()}.mp4"
    )
    vf = _filter_scale_pad(width, height)
    args = [_ffmpeg(), "-y", "-i", src]
    if not has_audio:
        args += [
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
    args += [
        "-vf",
        vf,
        "-map",
        "0:v:0",
    ]
    if has_audio:
        args += ["-map", "0:a:0"]
        # 音画对齐：
        # 1) asetpts=PTS-STARTPTS：把音频流起点归零（原素材音频 PTS 偏移/录制缺口
        #    导致声音比画面晚开始，内容整体平移对齐，这是音画不同步的主因）；
        # 2) aresample=async=1:first_pts=0：修正采样节奏漂移/微小抖动。
        af = "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"
        if normalize_audio:
            af = f"loudnorm=I=-16:TP=-1.5:LRA=11,{af}"
        args += ["-af", af]
    else:
        args += ["-map", "1:a:0"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-video_track_timescale",
        "15360",
        "-shortest",
        tmp_cache,
    ]
    run_ffmpeg(args, cancel_event, pause_event, log)
    os.replace(tmp_cache, cached)
    shutil.copy2(cached, dst)


def concat_two(
    head: str,
    tail: str,
    dst: str,
    head_duration: float,
    tail_duration: float,
    cancel_event,
    pause_event,
    transition_type: Optional[str] = None,
    transition_duration: float = 0.5,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    args = [_ffmpeg(), "-y", "-i", head, "-i", tail]
    can_transition = (
        transition_type
        and head_duration >= transition_duration + 0.2
        and tail_duration >= transition_duration + 0.2
    )
    if can_transition:
        duration = min(transition_duration, head_duration - 0.2, tail_duration - 0.2)
        offset = head_duration - duration
        # xfade 在 ffmpeg 7.1 会把输出自动协商为 yuv444p，末尾强制回 yuv420p
        # （体积小、兼容性好），否则成片体积明显变大。
        fc = (
            f"[0:v][1:v]xfade=transition={transition_type}:duration={duration:.3f}:offset={offset:.3f}[vraw];"
            f"[0:a][1:a]acrossfade=d={duration:.3f}:c1=tri:c2=tri[a];"
            f"[vraw]format=yuv420p[v]"
        )
    else:
        fc = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]"
    args += [
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        dst,
    ]
    run_ffmpeg(args, cancel_event, pause_event, log)


def concat_three(
    first: str,
    second: str,
    third: str,
    dst: str,
    durations: list[float],
    cancel_event,
    pause_event,
    transition_type: Optional[str] = None,
    transition_duration: float = 0.5,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    can_transition = (
        transition_type
        and len(durations) == 3
        and all(d >= transition_duration + 0.2 for d in durations)
    )
    if can_transition:
        d = min(transition_duration, *(x - 0.2 for x in durations))
        o1 = durations[0] - d
        o2 = durations[0] + durations[1] - 2 * d
        fc = (
            f"[0:v][1:v]xfade=transition={transition_type}:duration={d:.3f}:offset={o1:.3f}[v1];"
            f"[v1][2:v]xfade=transition={transition_type}:duration={d:.3f}:offset={o2:.3f}[vraw];"
            f"[0:a][1:a]acrossfade=d={d:.3f}:c1=tri:c2=tri[a1];"
            f"[a1][2:a]acrossfade=d={d:.3f}:c1=tri:c2=tri[a];"
            f"[vraw]format=yuv420p[v]"
        )
    else:
        fc = "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]"
    args = [
        _ffmpeg(),
        "-y",
        "-i",
        first,
        "-i",
        second,
        "-i",
        third,
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        dst,
    ]
    run_ffmpeg(args, cancel_event, pause_event, log)


def concat_chain(
    clips: list[str],
    dst: str,
    durations: list[float],
    cancel_event,
    pause_event,
    transition_type: Optional[str] = None,
    transition_duration: float = 0.5,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """通用 N 片段拼接：转场可用时链式 xfade，否则 concat 滤镜硬接。"""
    n = len(clips)
    if n < 2:
        raise MediaError("拼接至少需要两个片段。")
    if n == 2:
        concat_two(clips[0], clips[1], dst, durations[0], durations[1],
                   cancel_event, pause_event, transition_type, transition_duration, log)
        return
    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", clip]
    can_transition = (
        transition_type
        and len(durations) == n
        and all(d >= transition_duration + 0.2 for d in durations)
    )
    if can_transition:
        d = min(transition_duration, *(x - 0.2 for x in durations))
        # 链式 xfade：第 i 个 xfade 的输入是前 i 段拼接输出，其时长已减去 i 个转场重叠，
        # offset 必须用递推的"当前拼接输出时长 - d"，否则 offset 超出输入时长会被 ffmpeg 截断成片。
        v_parts: list[str] = []
        acc = durations[0]
        for i in range(n - 1):
            off = acc - d
            out_label = "[v]" if i == n - 2 else f"[v{i + 1}]"
            if i == 0:
                v_parts.append(f"[0:v][1:v]xfade=transition={transition_type}:duration={d:.3f}:offset={off:.3f}{out_label}")
            else:
                v_parts.append(f"[v{i}][{i + 1}:v]xfade=transition={transition_type}:duration={d:.3f}:offset={off:.3f}{out_label}")
            acc = off + durations[i + 1]
        a_parts = [f"[0:a][1:a]acrossfade=d={d:.3f}:c1=tri:c2=tri[a1]"]
        for i in range(1, n - 1):
            out_label = "[a]" if i == n - 2 else f"[a{i + 1}]"
            a_parts.append(f"[a{i}][{i + 1}:a]acrossfade=d={d:.3f}:c1=tri:c2=tri{out_label}")
        # xfade 在 ffmpeg 7.1 会把输出自动协商为 yuv444p，末尾强制回 yuv420p
        fc = ";".join(v_parts + a_parts) + ";[v]format=yuv420p[v]"
    else:
        streams = []
        for i in range(n):
            streams += [f"[{i}:v]", f"[{i}:a]"]
        fc = "".join(streams) + f"concat=n={n}:v=1:a=1[v][a]"
    args = (
        [_ffmpeg(), "-y"]
        + inputs
        + [
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            dst,
        ]
    )
    run_ffmpeg(args, cancel_event, pause_event, log)


def concat_copy(clips: list[str], dst: str, cancel_event, pause_event, log: Optional[Callable[[str], None]] = None) -> None:
    """无转场且素材已归一化时，用 concat demuxer 流复制拼接，避免重编码。"""
    tempdir = Path(tempfile.mkdtemp(prefix="sppj_concat_"))
    try:
        list_file = tempdir / "list.txt"
        lines = [f"file '{p.replace(chr(39), chr(39) + chr(92) + chr(39))}'" for p in clips]
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        args = [
            _ffmpeg(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            dst,
        ]
        run_ffmpeg(args, cancel_event, pause_event, log)
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def trim_duration(
    src: str,
    dst: str,
    limit: Optional[float],
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    if not limit:
        shutil.copyfile(src, dst)
        return
    args = [_ffmpeg(), "-y", "-i", src, "-t", f"{limit:.3f}", "-c", "copy", dst]
    run_ffmpeg(args, cancel_event, pause_event, log)


def mix_bgm(
    src: str,
    bgm: str,
    dst: str,
    volume: float,
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
    fade: bool = False,
    ducking: bool = False,
    bgm_duration: float = 0.0,
) -> None:
    bgm_chain = f"[1:a]volume={volume:.2f}"
    if fade:
        fade_duration = min(1.2, max(0.1, bgm_duration * 0.2)) if bgm_duration > 0 else 1.0
        fade_start = max(0.0, bgm_duration - fade_duration) if bgm_duration > 0 else 0.0
        bgm_chain += f",afade=t=in:d={fade_duration:.3f},afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}"
    bgm_chain += "[bgm]"
    if ducking:
        fc = (
            f"{bgm_chain};"
            "[0:a]asplit=2[voice][mixvoice];"
            "[bgm][voice]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=200[duckbgm];"
            "[mixvoice][duckbgm]amix=inputs=2:duration=first:dropout_transition=3[a]"
        )
    else:
        fc = f"{bgm_chain};[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[a]"
    args = [
        _ffmpeg(),
        "-y",
        "-i",
        src,
        "-stream_loop",
        "-1",
        "-i",
        bgm,
        "-filter_complex",
        fc,
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        dst,
    ]
    run_ffmpeg(args, cancel_event, pause_event, log)


def generate_bgm_wav(dst: str, duration: float = 32.0, sample_rate: int = 44100) -> None:
    duration = max(duration, 8.0)
    t = np.arange(int(sample_rate * duration), dtype=np.float64) / sample_rate
    chords = [
        [261.63, 329.63, 392.00, 493.88],
        [220.00, 261.63, 329.63, 440.00],
        [174.61, 220.00, 261.63, 349.23],
        [196.00, 246.94, 293.66, 392.00],
    ]
    chord_seconds = 2.0
    audio = np.zeros(t.shape[0], dtype=np.float64)
    for idx, chord in enumerate(chords):
        start = idx * chord_seconds
        end = min(start + chord_seconds, duration)
        mask = (t >= start) & (t < end)
        if not np.any(mask):
            continue
        local_t = t[mask] - start
        seg = np.zeros_like(local_t)
        for i, freq in enumerate(chord):
            detune = 1.0 + (i % 2) * 0.0015
            seg += np.sin(2 * math.pi * freq * detune * local_t)
            seg += 0.35 * np.sin(2 * math.pi * freq * 2 * local_t)
            seg += 0.12 * np.sin(2 * math.pi * freq * 3 * local_t)
        attack = np.minimum(local_t / 0.35, 1.0)
        release = np.minimum((chord_seconds - local_t) / 0.5, 1.0)
        envelope = np.clip(attack, 0, 1) * np.clip(release, 0, 1)
        audio[mask] = seg * envelope
    audio = audio / (np.max(np.abs(audio)) + 1e-9) * 0.28
    stereo = np.column_stack((audio, audio * 0.97))
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(dst, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def burn_subtitles(
    src: str,
    srt: str,
    dst: str,
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    escaped = escape_filter_path(srt)
    style = (
        "FontName=Microsoft YaHei,FontSize=16,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
        "BorderStyle=1,Outline=1,Shadow=0,Alignment=2,MarginV=28"
    )
    vf = f"subtitles=filename='{escaped}':force_style='{style}'"
    args = [
        _ffmpeg(),
        "-y",
        "-i",
        src,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        dst,
    ]
    run_ffmpeg(args, cancel_event, pause_event, log)


def apply_watermark(
    src: str,
    watermark: str,
    dst: str,
    width: int,
    height: int,
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
    mode: str = "铺满全屏",
    position: str = "右下角",
    scale: float = 0.15,
    opacity: float = 0.6,
) -> None:
    if mode == "角落水印":
        target_w = max(40, int(width * max(0.05, min(0.6, scale))))
        opacity = max(0.05, min(1.0, opacity))
        margin = max(12, int(width * 0.02))
        pos_map = {
            "右下角": f"main_w-overlay_w-{margin}:main_h-overlay_h-{margin}",
            "右上角": f"main_w-overlay_w-{margin}:{margin}",
            "左下角": f"{margin}:main_h-overlay_h-{margin}",
            "左上角": f"{margin}:{margin}",
        }
        pos = pos_map.get(position, pos_map["右下角"])
        fc = (
            f"[1:v]scale={target_w}:-2,format=rgba,colorchannelmixer=aa={opacity:.2f}[wm];"
            f"[0:v][wm]overlay={pos}:format=auto:shortest=1[v]"
        )
    else:
        fc = (
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}[wm];"
            "[0:v][wm]overlay=0:0:format=auto:shortest=1[v]"
        )
    args = [
        _ffmpeg(),
        "-y",
        "-i",
        src,
        "-loop",
        "1",
        "-i",
        watermark,
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        dst,
    ]
    run_ffmpeg(args, cancel_event, pause_event, log)


def get_thumbnail(path: str, max_width: int = 320) -> Optional[str]:
    """抽取素材缩略图（带缓存），失败返回 None。"""
    if not Path(path).exists():
        return None
    cached = _thumb_cache_path(path, max_width)
    if cached.exists() and cached.stat().st_size > 0:
        return str(cached)
    info = probe_media(path)
    if not info.get("ok"):
        return None
    seek = min(0.5, info["duration"] / 3.0)
    tmp = cached.with_name(f"{cached.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
    args = [
        _ffmpeg(),
        "-y",
        "-ss",
        f"{seek:.3f}",
        "-i",
        path,
        "-frames:v",
        "1",
        "-vf",
        f"scale='min({max_width},iw)':-2",
        "-q:v",
        "4",
        tmp,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            timeout=30,
        )
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return None
        os.replace(tmp, cached)
        return str(cached)
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def build_combinations(
    head_files: list[str],
    tail_files: list[str],
    fixed_head: Optional[str],
    fixed_tail: Optional[str],
    count: int,
    seed: int = 20260905,
    dedupe_enabled: bool = True,
) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    if fixed_head and fixed_tail:
        return [(fixed_head, fixed_tail)]
    if fixed_head:
        unique = list(dict.fromkeys(tail_files))
        rng.shuffle(unique)
        result = []
        while len(result) < count:
            result.extend(unique)
        return [(fixed_head, tail) for tail in result[:count]]
    if fixed_tail:
        unique = list(dict.fromkeys(head_files))
        rng.shuffle(unique)
        result = []
        while len(result) < count:
            result.extend(unique)
        return [(head, fixed_tail) for head in result[:count]]

    pairs = [(h, t) for h in head_files for t in tail_files]
    if not pairs:
        return []
    if not dedupe_enabled:
        return [(rng.choice(head_files), rng.choice(tail_files)) for _ in range(count)]

    if count <= len(pairs):
        batch = list(pairs)
        rng.shuffle(batch)
        return batch[:count]

    result = []
    while len(result) < count:
        batch = list(pairs)
        rng.shuffle(batch)
        result.extend(batch)
    return result[:count]


@dataclass
class JobConfig:
    head_folder: str
    tail_folder: str
    fixed_head: Optional[str] = None
    fixed_tail: Optional[str] = None
    output_folder: str = ""
    count: int = 10
    resolution: str = "1080x1920"
    duration_mode: str = "不限制"
    use_watermark: bool = False
    watermark_path: str = ""
    use_transition: bool = False
    transition_mode: str = "不使用"
    transition_type: str = "fade"
    transition_duration: float = 0.5
    transition_types: list[str] = field(default_factory=list)
    bgm_mode: str = "不使用"
    bgm_path: str = ""
    bgm_folder: str = ""
    fixed_bgm: Optional[str] = None
    bgm_volume: float = 0.2
    normalize_audio: bool = False
    bgm_fade: bool = False
    bgm_ducking: bool = False
    output_name_template: str = "output_{序号}_{开头}_{结尾}"
    random_seed: int = 20260905
    dedupe_enabled: bool = True
    middle_folder: str = ""
    fixed_middle: Optional[str] = None
    middle_items: list[str] = field(default_factory=list)
    middle_count: Optional[int] = None
    middle_pools: list[dict] = field(default_factory=list)
    use_subtitle: bool = False
    watermark_mode: str = "铺满全屏"
    watermark_position: str = "右下角"
    watermark_scale: float = 0.15
    watermark_opacity: float = 0.6
    workers: int = 2


@dataclass
class BatchResult:
    success: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
    failed_items: list[dict] = field(default_factory=list)
    success_items: list[dict] = field(default_factory=list)


def resolve_duration_mode(mode: str) -> Optional[float]:
    mapping = {"15s": 15.0, "25s": 25.0, "30s": 30.0}
    return mapping.get(mode)


def resolve_resolution(value: str) -> tuple[int, int]:
    if value == "1080x1920":
        return 1080, 1920
    return 1920, 1080


DEFAULT_TRANSITIONS = [
    "fade", "dissolve",
    "slideleft", "slideright", "slideup", "slidedown",
    "wipeleft", "wiperight", "wipeup", "wipedown",
    "circleopen", "circleclose", "circlecrop",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "zoomin", "pixelize", "fadeblack", "fadewhite",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
    "vertopen", "vertclose", "horzopen", "horzclose",
    "squeezeh", "squeezev",
]


def pick_transition(config: "JobConfig", index: int) -> Optional[str]:
    if config.transition_mode == "固定":
        return config.transition_type or "fade"
    if config.transition_mode == "随机":
        pool = config.transition_types or DEFAULT_TRANSITIONS
        if not pool:
            return None
        rng = random.Random(config.random_seed + index)
        return rng.choice(pool)
    if config.use_transition:
        return config.transition_type or "fade"
    return None


def build_middle_pools(config: "JobConfig") -> list[dict]:
    """标准化中间素材池列表：优先使用 middle_pools（多池），否则回退旧单池字段。

    每项结构：{"folder", "files", "items", "count"}
    - items：该池固定勾选的多条素材路径（按顺序插入）
    - count：未勾选时从该池随机抽取条数（None 表示兼容旧行为，默认抽 1 条）
    """
    pools: list[dict] = []
    if config.middle_pools:
        for pool in config.middle_pools:
            folder = str(pool.get("folder", "")).strip()
            if not folder:
                continue
            files = scan_videos(folder)
            items = [str(x) for x in pool.get("items", []) if str(x)][:10]
            count_raw = pool.get("count")
            count = None if count_raw is None else max(0, min(10, int(count_raw)))
            pools.append({"folder": folder, "files": files, "items": items, "count": count})
    elif config.middle_folder:
        files = scan_videos(config.middle_folder)
        items = [str(x) for x in (config.middle_items or []) if str(x)]
        if config.fixed_middle and not items:
            items = [config.fixed_middle]
        pools.append(
            {
                "folder": config.middle_folder,
                "files": files,
                "items": items,
                "count": config.middle_count,
            }
        )
    return pools


def pick_middle_sequence(
    pools: list[dict], index: int, random_seed: int
) -> list[str]:
    """按池顺序生成一条成片的中间片段序列：每池固定勾选优先，否则随机抽取 count 条。"""
    seq: list[str] = []
    for pi, pool in enumerate(pools):
        files = pool.get("files") or []
        if not files:
            continue
        items = [p for p in (pool.get("items") or []) if p in files]
        if items:
            seq.extend(items)
            continue
        count = pool.get("count")
        if count is None:
            count = 1
        count = max(0, min(10, int(count)))
        if count <= 0:
            continue
        rng = random.Random(random_seed + index * 100 + pi)
        shuffled = files[:]
        rng.shuffle(shuffled)
        seq.extend(shuffled[: min(count, len(shuffled))])
    return seq


def middle_display(middle_items: list[str]) -> Optional[str]:
    if not middle_items:
        return None
    return "、".join(Path(p).name for p in middle_items)


def pick_bgm(config: "JobConfig", index: int, bgm_files: list[str]) -> Optional[str]:
    if config.bgm_mode == "音乐文件夹固定":
        return config.fixed_bgm
    if config.bgm_mode == "音乐文件夹随机":
        if not bgm_files:
            return None
        rng = random.Random(config.random_seed + index)
        return rng.choice(bgm_files)
    if config.bgm_mode == "本地导入":
        return config.bgm_path
    return None


def render_output_name(
    template: str,
    index: int,
    head: str,
    tail: str,
    middle: Optional[str] = None,
) -> str:
    def clean(value: str) -> str:
        for ch in '\\/:*?"<>|':
            value = value.replace(ch, "_")
        return value.strip(" .")

    values = {
        "{序号}": f"{index:03d}",
        "{开头}": clean(Path(head).stem),
        "{结尾}": clean(Path(tail).stem),
        "{中间}": clean(Path(middle).stem) if middle else "无",
        "{日期}": time.strftime("%Y%m%d"),
    }
    result = template or "output_{序号}_{开头}_{结尾}"
    for key, value in values.items():
        result = result.replace(key, value)
    return clean(result) + ".mp4"


def _unique_output_name(output_dir: Path, idx: int) -> str:
    stamp = time.strftime("%H%M%S")
    name = f"output_{idx:03d}_{stamp}.mp4"
    if not (output_dir / name).exists():
        return name
    n = 1
    while True:
        candidate = f"output_{idx:03d}_{stamp}_{n}.mp4"
        if not (output_dir / candidate).exists():
            return candidate
        n += 1


def precheck_materials(config: "JobConfig") -> dict:
    """批量预检素材，返回每类素材的健康状态，坏文件提前标出。"""
    middle_pool_files: list[str] = []
    for pool in build_middle_pools(config):
        middle_pool_files.extend(pool.get("files") or [])
    middle_files: list[str] = []
    for p in middle_pool_files:
        if p not in middle_files:
            middle_files.append(p)
    groups: dict[str, list[str]] = {
        "head": scan_videos(config.head_folder),
        "tail": scan_videos(config.tail_folder),
        "middle": middle_files,
        "bgm": scan_audio(config.bgm_folder) if config.bgm_folder else [],
    }
    out: dict[str, list[dict]] = {}
    for kind, files in groups.items():
        items = []
        for path in files:
            try:
                info = probe_media(path)
                items.append(
                    {
                        "path": path,
                        "name": Path(path).name,
                        "ok": info.get("ok", True),
                        "duration": round(info.get("duration", 0.0), 2),
                        "has_audio": info.get("has_audio", False),
                        "width": info.get("width", 0),
                        "height": info.get("height", 0),
                        "error": "" if info.get("ok", True) else "无法读取视频流",
                    }
                )
            except Exception as exc:
                items.append(
                    {
                        "path": path,
                        "name": Path(path).name,
                        "ok": False,
                        "duration": 0.0,
                        "has_audio": False,
                        "width": 0,
                        "height": 0,
                        "error": str(exc),
                    }
                )
        out[kind] = items
    return out


def _process_one_item(
    idx: int,
    head: str,
    tail: str,
    middle_items: list[str],
    final_path: Path,
    config: "JobConfig",
    width: int,
    height: int,
    duration_limit: Optional[float],
    bgm_files: list[str],
    cancel_event,
    pause_event,
    log: Callable[[str], None],
    retry_count: int,
    path_locks: dict,
) -> dict:
    """处理单个组合，返回结果字典。由 worker 线程调用。"""
    if cancel_event.is_set():
        return {"index": idx, "state": "cancelled"}
    while pause_event.is_set() and not cancel_event.is_set():
        time.sleep(0.2)
    if cancel_event.is_set():
        return {"index": idx, "state": "cancelled"}

    if final_path.exists() and final_path.stat().st_size > 0:
        log(f"[{idx}] 已存在，跳过：{final_path}")
        return {"index": idx, "state": "skipped", "output": str(final_path)}

    lock = path_locks.setdefault(str(final_path), threading.Lock())
    with lock:
        if final_path.exists() and final_path.stat().st_size > 0:
            log(f"[{idx}] 已存在，跳过：{final_path}")
            return {"index": idx, "state": "skipped", "output": str(final_path)}
        middle_desc = " + ".join(Path(p).name for p in middle_items)
        if middle_desc:
            middle_desc = " + " + middle_desc
        log(f"[{idx}] 开始生成：{Path(head).name}{middle_desc} + {Path(tail).name}")
        last_error = None
        for attempt in range(retry_count + 1):
            if cancel_event.is_set():
                return {"index": idx, "state": "cancelled"}
            try:
                _process_one_combo(
                    head,
                    tail,
                    final_path,
                    middle_items,
                    pick_transition(config, idx),
                    pick_bgm(config, idx, bgm_files),
                    width,
                    height,
                    duration_limit,
                    config,
                    cancel_event,
                    pause_event,
                    log,
                )
                log(f"[{idx}] 完成：{final_path}")
                return {
                    "index": idx, "state": "success", "head": head, "tail": tail,
                    "middle": middle_display(middle_items),
                    "middle_files": middle_items,
                    "output": str(final_path),
                }
            except CancelledError:
                return {"index": idx, "state": "cancelled"}
            except Exception as exc:
                last_error = exc
                log(f"[{idx}] 第 {attempt + 1} 次失败：{exc}")
                if attempt < retry_count:
                    time.sleep(1)
        return {
            "index": idx, "state": "failed", "head": head, "tail": tail,
            "middle": middle_display(middle_items),
            "middle_files": middle_items,
            "error": str(last_error),
        }


def process_batch(
    config: JobConfig,
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    retry_count: int = 2,
    skip_existing: bool = True,
) -> BatchResult:
    result = BatchResult()
    head_files = scan_videos(config.head_folder)
    tail_files = scan_videos(config.tail_folder)
    middle_pools = build_middle_pools(config)
    bgm_files = scan_audio(config.bgm_folder) if config.bgm_folder else []
    if not head_files:
        raise MediaError("开头文件夹中没有找到视频文件。")
    if not tail_files:
        raise MediaError("结尾文件夹中没有找到视频文件。")

    if config.fixed_head and config.fixed_head not in head_files:
        raise MediaError("固定开头不在开头文件夹中，请重新选择。")
    if config.fixed_tail and config.fixed_tail not in tail_files:
        raise MediaError("固定结尾不在结尾文件夹中，请重新选择。")
    for pool in middle_pools:
        missing_items = [p for p in (pool.get("items") or []) if p not in pool.get("files", [])]
        if missing_items:
            raise MediaError(
                f"中间素材池 {Path(pool['folder']).name} 中的以下固定素材不在该文件夹中："
                f"{'、'.join(Path(p).name for p in missing_items)}"
            )
    if config.bgm_mode == "音乐文件夹固定" and config.fixed_bgm and config.fixed_bgm not in bgm_files:
        raise MediaError("固定 BGM 不在音乐文件夹中，请重新选择。")

    count = max(1, min(200, int(config.count)))
    if config.fixed_head and config.fixed_tail:
        count = 1
    combos = build_combinations(
        head_files,
        tail_files,
        config.fixed_head,
        config.fixed_tail,
        count,
        config.random_seed,
        config.dedupe_enabled,
    )
    if not combos:
        raise MediaError("没有可生成的素材组合。")

    output_dir = Path(config.output_folder or Path(config.head_folder).parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    width, height = resolve_resolution(config.resolution)
    duration_limit = resolve_duration_mode(config.duration_mode)

    logger = _make_task_logger(log)
    task_id = time.strftime("%Y%m%d_%H%M%S")
    logger(f"任务 {task_id} 开始，共 {len(combos)} 条，并发 {config.workers}")

    workers = max(1, min(8, int(config.workers or 1)))
    path_locks: dict = {}
    done_count = 0
    done_lock = threading.Lock()

    def on_done() -> None:
        nonlocal done_count
        with done_lock:
            done_count += 1
            current = done_count
        if progress:
            progress(current, len(combos))

    if workers <= 1:
        for idx, (head, tail) in enumerate(combos, 1):
            middle_items = pick_middle_sequence(middle_pools, idx, config.random_seed)
            first_middle = middle_items[0] if middle_items else None
            final_name = render_output_name(config.output_name_template, idx, head, tail, first_middle)
            final_path = output_dir / final_name
            if skip_existing and final_path.exists() and final_path.stat().st_size > 0:
                result.skipped += 1
                on_done()
                continue
            item = _process_one_item(
                idx, head, tail, middle_items, final_path, config, width, height,
                duration_limit, bgm_files, cancel_event, pause_event, logger,
                retry_count, path_locks,
            )
            _collect_item(result, item)
            on_done()
    else:
        tasks = []
        for idx, (head, tail) in enumerate(combos, 1):
            middle_items = pick_middle_sequence(middle_pools, idx, config.random_seed)
            first_middle = middle_items[0] if middle_items else None
            final_name = render_output_name(config.output_name_template, idx, head, tail, first_middle)
            final_path = output_dir / final_name
            tasks.append((idx, head, tail, middle_items, final_path))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for idx, head, tail, middle_items, final_path in tasks:
                if cancel_event.is_set():
                    result.cancelled = True
                    break
                if skip_existing and final_path.exists() and final_path.stat().st_size > 0:
                    result.skipped += 1
                    on_done()
                    continue
                fut = executor.submit(
                    _process_one_item,
                    idx, head, tail, middle_items, final_path, config, width, height,
                    duration_limit, bgm_files, cancel_event, pause_event, logger,
                    retry_count, path_locks,
                )
                futures[fut] = idx
            for fut in as_completed(futures):
                try:
                    item = fut.result()
                except Exception as exc:
                    item = {"index": futures[fut], "state": "failed", "error": str(exc)}
                _collect_item(result, item)
                on_done()

    if progress:
        progress(len(combos), len(combos))
    logger(
        f"任务 {task_id} 结束：成功 {result.success}，跳过 {result.skipped}，失败 {result.failed}"
    )
    return result


def _collect_item(result: BatchResult, item: dict) -> None:
    state = item.get("state")
    if state == "success":
        result.success += 1
        result.success_items.append(
            {
                "index": item["index"],
                "head": item.get("head", ""),
                "tail": item.get("tail", ""),
                "middle": item.get("middle"),
                "middle_files": item.get("middle_files") or [],
                "output": item.get("output", ""),
            }
        )
    elif state == "failed":
        result.failed += 1
        message = f"[{item['index']}] 最终失败：{item.get('error', '')}"
        result.errors.append(message)
        result.failed_items.append(
            {
                "index": item["index"],
                "head": item.get("head", ""),
                "tail": item.get("tail", ""),
                "middle": item.get("middle"),
                "middle_files": item.get("middle_files") or [],
                "error": item.get("error", ""),
            }
        )
    elif state == "skipped":
        result.skipped += 1
    elif state == "cancelled":
        result.cancelled = True


def _process_one_combo(
    head: str,
    tail: str,
    final_path: Path,
    middle_items: list[str],
    transition_type: Optional[str],
    selected_bgm: Optional[str],
    width: int,
    height: int,
    duration_limit: Optional[float],
    config: JobConfig,
    cancel_event,
    pause_event,
    log: Callable[[str], None],
) -> None:
    tempdir = Path(tempfile.mkdtemp(prefix="sppj_"))
    try:
        head_info = probe_media(head)
        tail_info = probe_media(tail)
        head_norm = str(tempdir / "head_norm.mp4")
        tail_norm = str(tempdir / "tail_norm.mp4")
        concat_path = str(tempdir / "concat.mp4")
        current = concat_path

        normalize_clip(
            head, head_norm, width, height, head_info["duration"], head_info["has_audio"],
            cancel_event, pause_event, log, config.normalize_audio,
        )
        normalize_clip(
            tail, tail_norm, width, height, tail_info["duration"], tail_info["has_audio"],
            cancel_event, pause_event, log, config.normalize_audio,
        )

        clips = [head_norm]
        # 转场 offset 必须以归一化后文件的实际时长为依据（-shortest 等会使
        # 原素材 probe 时长与归一化文件存在偏差），否则 xfade 过渡点与
        # acrossfade 边界错位、音画不同步且随片段数累积。
        durations = [probe_media(head_norm)["duration"]]
        for i, middle in enumerate(middle_items, 1):
            middle_info = probe_media(middle)
            middle_norm = str(tempdir / f"middle_norm_{i}.mp4")
            normalize_clip(
                middle, middle_norm, width, height, middle_info["duration"], middle_info["has_audio"],
                cancel_event, pause_event, log, config.normalize_audio,
            )
            clips.append(middle_norm)
            durations.append(probe_media(middle_norm)["duration"])
        clips.append(tail_norm)
        durations.append(probe_media(tail_norm)["duration"])

        n_clips = len(clips)
        if n_clips == 2:
            if transition_type:
                concat_two(
                    clips[0], clips[1], concat_path,
                    durations[0], durations[1],
                    cancel_event, pause_event, transition_type, config.transition_duration, log,
                )
            else:
                # 无转场且素材已统一归一化，使用流复制快路径
                concat_copy(clips, concat_path, cancel_event, pause_event, log)
        else:
            concat_chain(
                clips, concat_path, durations,
                cancel_event, pause_event, transition_type, config.transition_duration, log,
            )

        total_duration = sum(durations)
        if duration_limit and total_duration > duration_limit:
            trimmed = str(tempdir / "trimmed.mp4")
            trim_duration(concat_path, trimmed, duration_limit, cancel_event, pause_event, log)
            current = trimmed

        if config.bgm_mode in {"本地导入", "算法生成", "音乐文件夹固定", "音乐文件夹随机"}:
            if config.bgm_mode == "算法生成":
                bgm = str(tempdir / "bgm.wav")
                log("正在生成背景音乐...")
                generate_bgm_wav(bgm, max(32.0, total_duration))
                bgm_duration = max(32.0, total_duration)
            else:
                bgm = selected_bgm or config.bgm_path
                if not bgm:
                    raise MediaError("没有可用的 BGM 文件")
                bgm_info = probe_media(bgm)
                bgm_duration = bgm_info["duration"]
            mixed = str(tempdir / "with_bgm.mp4")
            mix_bgm(
                current, bgm, mixed, float(config.bgm_volume),
                cancel_event, pause_event, log,
                fade=config.bgm_fade, ducking=config.bgm_ducking, bgm_duration=bgm_duration,
            )
            current = mixed

        if config.use_subtitle:
            if not subtitle_plugin.available():
                raise subtitle_plugin.SubtitleUnavailableError(
                    "自动字幕需要 faster-whisper。请运行：pip install faster-whisper"
                )
            srt_path = str(tempdir / "subtitle.srt")
            subtitle_plugin.generate_subtitles(head, tail, head_info["duration"], srt_path)
            subtitled = str(tempdir / "with_subtitle.mp4")
            burn_subtitles(current, srt_path, subtitled, cancel_event, pause_event, log)
            current = subtitled

        if config.use_watermark:
            if not config.watermark_path:
                raise MediaError("已勾选水印，但未选择水印图片。")
            watermarked = str(tempdir / "with_watermark.mp4")
            apply_watermark(
                current, config.watermark_path, watermarked, width, height,
                cancel_event, pause_event, log,
                mode=config.watermark_mode, position=config.watermark_position,
                scale=config.watermark_scale, opacity=config.watermark_opacity,
            )
            current = watermarked

        shutil.move(current, final_path)
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _make_task_logger(
    callback: Optional[Callable[[str], None]],
) -> Callable[[str], None]:
    log_dir = _app_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"task_{time.strftime('%Y%m%d_%H%M%S')}.log"
    lock = threading.Lock()

    def write(message: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        if callback:
            callback(line)
        try:
            with lock:
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    return write


def process_failed_items(
    config: JobConfig,
    failed_items: list[dict],
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    retry_count: int = 2,
) -> BatchResult:
    result = BatchResult()
    if not failed_items:
        return result
    output_dir = Path(config.output_folder or Path(config.head_folder).parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    width, height = resolve_resolution(config.resolution)
    duration_limit = resolve_duration_mode(config.duration_mode)
    bgm_files = scan_audio(config.bgm_folder) if config.bgm_folder else []
    logger = _make_task_logger(log)
    logger(f"开始重试失败项，共 {len(failed_items)} 条")

    for pos, item in enumerate(failed_items, 1):
        idx = int(item.get("index", pos))
        head = str(item.get("head", ""))
        tail = str(item.get("tail", ""))
        middle_files_retry = [str(p) for p in (item.get("middle_files") or []) if str(p)]
        if not middle_files_retry and item.get("middle"):
            # 兼容旧记录：middle 是单条路径
            m = str(item.get("middle") or "")
            if m and not m.startswith("、") and "、" not in m:
                middle_files_retry = [m]
        if progress:
            progress(pos, len(failed_items))
        if cancel_event.is_set():
            result.cancelled = True
            break
        while pause_event.is_set() and not cancel_event.is_set():
            time.sleep(0.2)
        first_middle = middle_files_retry[0] if middle_files_retry else None
        final_path = output_dir / render_output_name(config.output_name_template, idx, head, tail, first_middle)
        if final_path.exists():
            final_path.unlink(missing_ok=True)
        middle_desc = " + ".join(Path(p).name for p in middle_files_retry)
        if middle_desc:
            middle_desc = " + " + middle_desc
        logger(f"[{pos}/{len(failed_items)}] 重试：{Path(head).name}{middle_desc} + {Path(tail).name}")
        last_error = None
        for attempt in range(retry_count + 1):
            try:
                _process_one_combo(
                    head, tail, final_path, middle_files_retry,
                    pick_transition(config, idx), pick_bgm(config, idx, bgm_files),
                    width, height, duration_limit, config,
                    cancel_event, pause_event, logger,
                )
                result.success += 1
                result.success_items.append(
                    {
                        "index": idx, "head": head, "tail": tail,
                        "middle": middle_display(middle_files_retry),
                        "middle_files": middle_files_retry,
                        "output": str(final_path),
                    }
                )
                last_error = None
                logger(f"[{pos}/{len(failed_items)}] 重试完成：{final_path}")
                break
            except CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger(f"[{pos}/{len(failed_items)}] 第 {attempt + 1} 次失败：{exc}")
                if attempt < retry_count:
                    time.sleep(1)
        else:
            result.failed += 1
            message = f"[{pos}/{len(failed_items)}] 最终失败：{last_error}"
            result.errors.append(message)
            result.failed_items.append(
                {
                    "index": idx, "head": head, "tail": tail,
                    "middle": middle_display(middle_files_retry),
                    "middle_files": middle_files_retry,
                    "error": str(last_error),
                }
            )
            logger(message)

    if progress:
        progress(len(failed_items), len(failed_items))
    return result
