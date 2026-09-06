import importlib.util
from pathlib import Path


class SubtitleUnavailableError(Exception):
    pass


def available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def generate_subtitles(
    head: str,
    tail: str,
    head_duration: float,
    srt_path: str,
) -> None:
    if not available():
        raise SubtitleUnavailableError(
            "自动字幕需要 faster-whisper。请运行：pip install faster-whisper"
        )

    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments_out = []

    def collect(path: str, offset: float) -> None:
        seg_iter, _ = model.transcribe(path, language="zh", vad_filter=True)
        for seg in seg_iter:
            text = seg.text.strip()
            if text:
                segments_out.append((offset + float(seg.start), offset + float(seg.end), text))

    collect(head, 0.0)
    collect(tail, head_duration)
    segments_out.sort(key=lambda x: x[0])
    _write_srt(segments_out, srt_path)


def _format_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    milli = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _write_srt(segments: list[tuple[float, float, str]], path: str) -> None:
    lines = []
    for idx, (start, end, text) in enumerate(segments, 1):
        lines.append(str(idx))
        lines.append(_format_srt_time(start) + " --> " + _format_srt_time(end))
        lines.append(text)
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
