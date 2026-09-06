import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe


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


def scan_videos(folder: str) -> list[str]:
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    items = sorted(
        [str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
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
    return {
        "duration": duration or 1.0,
        "has_audio": bool(re.search(r"Stream #\d+:\d+[^\n]*Audio:", text)),
        "has_video": bool(re.search(r"Stream #\d+:\d+[^\n]*Video:", text)),
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
) -> None:
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
        "-shortest",
        dst,
    ]
    run_ffmpeg(args, cancel_event, pause_event, log)


def concat_two(
    head: str,
    tail: str,
    dst: str,
    head_duration: float,
    tail_duration: float,
    transition: bool,
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    args = [_ffmpeg(), "-y", "-i", head, "-i", tail]
    if transition and head_duration >= 1.0 and tail_duration >= 1.0:
        transition_duration = min(0.5, head_duration - 0.2, tail_duration - 0.2)
        offset = head_duration - transition_duration
        fc = (
            f"[0:v][1:v]xfade=transition=fade:duration={transition_duration:.3f}:offset={offset:.3f}[v];"
            f"[0:a][1:a]acrossfade=d={transition_duration:.3f}:c1=tri:c2=tri[a]"
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
) -> None:
    fc = (
        f"[1:a]volume={volume:.2f}[bgm];"
        "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[a]"
    )
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


def apply_watermark(
    src: str,
    watermark: str,
    dst: str,
    cancel_event,
    pause_event,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    fc = (
        "[1:v]scale=iw*0.16:-1[wm];"
        "[0:v][wm]overlay=W-w-40:H-h-40:format=auto:shortest=1[v]"
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


def build_combinations(
    head_files: list[str],
    tail_files: list[str],
    fixed_head: Optional[str],
    fixed_tail: Optional[str],
    count: int,
) -> list[tuple[str, str]]:
    if fixed_head and fixed_tail:
        return [(fixed_head, fixed_tail)]
    if fixed_head:
        unique = list(dict.fromkeys(tail_files))
        return [(fixed_head, unique[i % len(unique)]) for i in range(count)]
    if fixed_tail:
        unique = list(dict.fromkeys(head_files))
        return [(unique[i % len(unique)], fixed_tail) for i in range(count)]

    pairs = [(h, t) for h in head_files for t in tail_files]
    if not pairs:
        return []
    import random

    rng = random.Random(20260905)
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
    bgm_mode: str = "不使用"
    bgm_path: str = ""
    bgm_volume: float = 0.2


@dataclass
class BatchResult:
    success: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
    failed_items: list[dict] = field(default_factory=list)


def resolve_duration_mode(mode: str) -> Optional[float]:
    mapping = {"15s": 15.0, "25s": 25.0, "30s": 30.0}
    return mapping.get(mode)


def resolve_resolution(value: str) -> tuple[int, int]:
    if value == "1080x1920":
        return 1080, 1920
    return 1920, 1080


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
    if not head_files:
        raise MediaError("开头文件夹中没有找到视频文件。")
    if not tail_files:
        raise MediaError("结尾文件夹中没有找到视频文件。")

    if config.fixed_head and config.fixed_head not in head_files:
        raise MediaError("固定开头不在开头文件夹中，请重新选择。")
    if config.fixed_tail and config.fixed_tail not in tail_files:
        raise MediaError("固定结尾不在结尾文件夹中，请重新选择。")

    count = max(1, min(200, int(config.count)))
    if config.fixed_head and config.fixed_tail:
        count = 1
    combos = build_combinations(
        head_files,
        tail_files,
        config.fixed_head,
        config.fixed_tail,
        count,
    )
    if not combos:
        raise MediaError("没有可生成的素材组合。")

    output_dir = Path(config.output_folder or Path(config.head_folder).parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    width, height = resolve_resolution(config.resolution)
    duration_limit = resolve_duration_mode(config.duration_mode)

    logger = _make_task_logger(log)
    task_id = time.strftime("%Y%m%d_%H%M%S")
    logger(f"任务 {task_id} 开始，共 {len(combos)} 条")

    for idx, (head, tail) in enumerate(combos, 1):
        if progress:
            progress(idx, len(combos))
        if cancel_event.is_set():
            result.cancelled = True
            break
        while pause_event.is_set() and not cancel_event.is_set():
            time.sleep(0.2)
        if cancel_event.is_set():
            result.cancelled = True
            break

        final_name = f"output_{idx:03d}.mp4"
        final_path = output_dir / final_name
        if skip_existing and final_path.exists() and final_path.stat().st_size > 0:
            result.skipped += 1
            logger(f"[{idx}/{len(combos)}] 已存在，跳过：{final_path}")
            continue

        logger(f"[{idx}/{len(combos)}] 开始生成：{Path(head).name} + {Path(tail).name}")
        last_error = None
        for attempt in range(retry_count + 1):
            if cancel_event.is_set():
                result.cancelled = True
                break
            try:
                _process_one_combo(
                    head,
                    tail,
                    final_path,
                    width,
                    height,
                    duration_limit,
                    config,
                    cancel_event,
                    pause_event,
                    logger,
                )
                result.success += 1
                last_error = None
                logger(f"[{idx}/{len(combos)}] 完成：{final_path}")
                break
            except CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger(f"[{idx}/{len(combos)}] 第 {attempt + 1} 次失败：{exc}")
                if attempt < retry_count:
                    time.sleep(1)
        else:
            result.failed += 1
            message = f"[{idx}/{len(combos)}] 最终失败：{last_error}"
            result.errors.append(message)
            result.failed_items.append(
                {
                    "index": idx,
                    "head": head,
                    "tail": tail,
                    "error": str(last_error),
                }
            )
            logger(message)

    if progress:
        progress(len(combos), len(combos))
    logger(
        f"任务 {task_id} 结束：成功 {result.success}，跳过 {result.skipped}，失败 {result.failed}"
    )
    return result


def _process_one_combo(
    head: str,
    tail: str,
    final_path: Path,
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
            head,
            head_norm,
            width,
            height,
            head_info["duration"],
            head_info["has_audio"],
            cancel_event,
            pause_event,
            log,
        )
        normalize_clip(
            tail,
            tail_norm,
            width,
            height,
            tail_info["duration"],
            tail_info["has_audio"],
            cancel_event,
            pause_event,
            log,
        )
        concat_two(
            head_norm,
            tail_norm,
            concat_path,
            head_info["duration"],
            tail_info["duration"],
            config.use_transition,
            cancel_event,
            pause_event,
            log,
        )

        if duration_limit and head_info["duration"] + tail_info["duration"] > duration_limit:
            trimmed = str(tempdir / "trimmed.mp4")
            trim_duration(concat_path, trimmed, duration_limit, cancel_event, pause_event, log)
            current = trimmed

        if config.bgm_mode in {"本地导入", "算法生成"}:
            bgm = config.bgm_path if config.bgm_mode == "本地导入" else str(tempdir / "bgm.wav")
            if config.bgm_mode == "算法生成":
                log("正在生成背景音乐...")
                generate_bgm_wav(bgm, max(32.0, head_info["duration"] + tail_info["duration"]))
            mixed = str(tempdir / "with_bgm.mp4")
            mix_bgm(
                current,
                bgm,
                mixed,
                float(config.bgm_volume),
                cancel_event,
                pause_event,
                log,
            )
            current = mixed

        if config.use_watermark:
            if not config.watermark_path:
                raise MediaError("已勾选水印，但未选择水印图片。")
            watermarked = str(tempdir / "with_watermark.mp4")
            apply_watermark(
                current,
                config.watermark_path,
                watermarked,
                cancel_event,
                pause_event,
                log,
            )
            current = watermarked

        shutil.move(current, final_path)
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _make_task_logger(
    callback: Optional[Callable[[str], None]],
) -> Callable[[str], None]:
    if getattr(sys, "frozen", False):
        log_dir = Path(sys.executable).resolve().parent / "logs"
    else:
        log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"task_{time.strftime('%Y%m%d_%H%M%S')}.log"

    def write(message: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        if callback:
            callback(line)
        try:
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
    logger = _make_task_logger(log)
    logger(f"开始重试失败项，共 {len(failed_items)} 条")

    for pos, item in enumerate(failed_items, 1):
        idx = int(item.get("index", pos))
        head = str(item.get("head", ""))
        tail = str(item.get("tail", ""))
        if progress:
            progress(pos, len(failed_items))
        if cancel_event.is_set():
            result.cancelled = True
            break
        while pause_event.is_set() and not cancel_event.is_set():
            time.sleep(0.2)
        final_path = output_dir / f"output_{idx:03d}.mp4"
        if final_path.exists():
            final_path.unlink(missing_ok=True)
        logger(f"[{pos}/{len(failed_items)}] 重试：{Path(head).name} + {Path(tail).name}")
        last_error = None
        for attempt in range(retry_count + 1):
            try:
                _process_one_combo(
                    head,
                    tail,
                    final_path,
                    width,
                    height,
                    duration_limit,
                    config,
                    cancel_event,
                    pause_event,
                    logger,
                )
                result.success += 1
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
                {"index": idx, "head": head, "tail": tail, "error": str(last_error)}
            )
            logger(message)

    if progress:
        progress(len(failed_items), len(failed_items))
    return result


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
