import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image
from imageio_ffmpeg import get_ffmpeg_exe

from video_engine import JobConfig, build_combinations, probe_media, process_batch, render_output_name, scan_videos


FFMPEG = get_ffmpeg_exe()


def make_clip(path: Path, color: str = "blue", duration: int = 2, size: str = "320x480") -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size={size}:duration={duration}:r=30",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def make_clip_with_audio(path: Path, color: str = "blue", duration: int = 2) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size=320x480:duration={duration}:r=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class CorePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="sppj_tests_"))
        self.head = self.temp / "head"
        self.tail = self.temp / "tail"
        self.output = self.temp / "output"
        self.head.mkdir()
        self.tail.mkdir()
        self.output.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _config(self, **overrides) -> JobConfig:
        values = {
            "head_folder": str(self.head),
            "tail_folder": str(self.tail),
            "output_folder": str(self.output),
            "count": 1,
            "resolution": "1080x1920",
            "duration_mode": "不限制",
            "bgm_mode": "不使用",
        }
        values.update(overrides)
        return JobConfig(**values)

    def test_deterministic_combinations(self) -> None:
        heads = ["h1.mp4", "h2.mp4", "h3.mp4"]
        tails = ["t1.mp4", "t2.mp4"]
        first = build_combinations(heads, tails, None, None, 6, seed=7)
        second = build_combinations(heads, tails, None, None, 6, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), 6)

    def test_render_output_name(self) -> None:
        name = render_output_name("{序号}_{开头}_{结尾}", 3, "head.mp4", "tail.mp4")
        self.assertEqual(name, "003_head_tail.mp4")

    def test_scan_and_probe(self) -> None:
        make_clip(self.head / "a.mp4", "blue")
        self.assertEqual(len(scan_videos(str(self.head))), 1)
        info = probe_media(str(self.head / "a.mp4"))
        self.assertTrue(info["has_video"])
        self.assertGreaterEqual(info["duration"], 1.0)

    def test_fixed_head_combinations(self) -> None:
        heads = ["h1.mp4", "h2.mp4"]
        tails = ["t1.mp4", "t2.mp4", "t3.mp4"]
        combos = build_combinations(heads, tails, "h1.mp4", None, 3)
        self.assertEqual(len(combos), 3)
        self.assertTrue(all(head == "h1.mp4" for head, _ in combos))

    def test_batch_generation(self) -> None:
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t1.mp4", "red")
        make_clip(self.tail / "t2.mp4", "green")
        result = process_batch(self._config(count=2), threading.Event(), threading.Event())
        self.assertEqual(result.success, 2)
        self.assertEqual(result.failed, 0)
        self.assertEqual(len(list(self.output.glob("*.mp4"))), 2)
        self.assertEqual(len(result.success_items), 2)

    def test_watermark_and_algorithm_bgm(self) -> None:
        make_clip_with_audio(self.head / "h.mp4", "blue")
        make_clip_with_audio(self.tail / "t.mp4", "red")
        watermark = self.temp / "wm.png"
        Image.new("RGBA", (120, 60), (0, 255, 0, 160)).save(watermark)
        result = process_batch(
            self._config(
                use_watermark=True,
                watermark_path=str(watermark),
                bgm_mode="算法生成",
                normalize_audio=True,
                bgm_fade=True,
                bgm_ducking=True,
            ),
            threading.Event(),
            threading.Event(),
        )
        self.assertEqual(result.success, 1)
        self.assertEqual(result.failed, 0)

    def test_cancel(self) -> None:
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        cancel = threading.Event()
        cancel.set()
        result = process_batch(self._config(count=2), cancel, threading.Event())
        self.assertTrue(result.cancelled)
        self.assertEqual(result.success, 0)

    def test_failure_isolation(self) -> None:
        make_clip(self.head / "good.mp4", "blue")
        make_clip(self.tail / "good.mp4", "red")
        (self.tail / "bad.mp4").write_bytes(b"not a real video")
        result = process_batch(self._config(count=2), threading.Event(), threading.Event())
        self.assertEqual(result.success, 1)
        self.assertEqual(result.failed, 1)
        self.assertTrue(result.errors)

    def test_skip_existing_output(self) -> None:
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        first = process_batch(self._config(count=1), threading.Event(), threading.Event())
        self.assertEqual(first.success, 1)
        second = process_batch(self._config(count=1), threading.Event(), threading.Event())
        self.assertEqual(second.skipped, 1)
        self.assertEqual(second.success, 0)


if __name__ == "__main__":
    unittest.main()
