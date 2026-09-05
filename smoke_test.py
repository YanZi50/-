import subprocess
import tempfile
import threading
import shutil
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe

from video_engine import JobConfig, process_batch


def make_clip(ffmpeg: str, path: Path, color: str, duration: int = 2) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size=320x480:duration={duration}:r=30",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="sppj_smoke_"))
    try:
        (root / "head").mkdir()
        (root / "tail").mkdir()
        (root / "output").mkdir()
        ffmpeg = get_ffmpeg_exe()
        make_clip(ffmpeg, root / "head" / "head.mp4", "blue")
        make_clip(ffmpeg, root / "tail" / "tail.mp4", "red")
        config = JobConfig(
            head_folder=str(root / "head"),
            tail_folder=str(root / "tail"),
            output_folder=str(root / "output"),
            count=2,
            resolution="1080x1920",
            bgm_mode="不使用",
        )
        result = process_batch(config, threading.Event(), threading.Event())
        assert result.success == 2, f"expected 2 successful outputs, got {result.success}"
        assert result.failed == 0, f"expected 0 failures, got {result.failed}"
        files = list((root / "output").glob("*.mp4"))
        assert len(files) == 2, f"expected 2 output files, got {len(files)}"
        print("smoke test passed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
