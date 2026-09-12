import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from video_engine import (
    JobConfig,
    cache_dir,
    concat_copy,
    get_thumbnail,
    normalize_clip,
    precheck_materials,
    process_batch,
    probe_media,
)
from tests.test_core import make_clip, make_clip_with_audio


class EngineV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="sppj_v2_"))
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
            "count": 2,
            "resolution": "1080x1920",
            "duration_mode": "不限制",
            "bgm_mode": "不使用",
        }
        values.update(overrides)
        return JobConfig(**values)

    def test_norm_cache_reused_across_batches(self) -> None:
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        logs: list[str] = []
        first = process_batch(self._config(count=1), threading.Event(), threading.Event(), log=logs.append)
        self.assertEqual(first.success, 1)
        norm_dir = cache_dir() / "norm"
        self.assertTrue(norm_dir.exists())
        cached_before = sorted(p.name for p in norm_dir.glob("*.mp4"))
        self.assertTrue(cached_before, "归一化缓存应已生成")

        # 换一个输出目录，让第二次任务真正执行生成流程而非跳过
        second_out = self.temp / "output2"
        second_out.mkdir()
        logs.clear()
        second = process_batch(
            self._config(count=1, output_folder=str(second_out)),
            threading.Event(), threading.Event(), log=logs.append,
        )
        self.assertEqual(second.success, 1)
        hits = [line for line in logs if "命中归一化缓存" in line]
        self.assertEqual(len(hits), 2, "第二次任务应命中两个素材的归一化缓存")

    def test_workers_consistency(self) -> None:
        make_clip(self.head / "h1.mp4", "blue")
        make_clip(self.head / "h2.mp4", "green")
        make_clip(self.tail / "t1.mp4", "red")
        make_clip(self.tail / "t2.mp4", "yellow")
        serial = process_batch(
            self._config(count=4, workers=1), threading.Event(), threading.Event()
        )
        self.assertEqual(serial.success, 4)
        shutil.rmtree(self.output, ignore_errors=True)
        self.output.mkdir()
        parallel = process_batch(
            self._config(count=4, workers=4), threading.Event(), threading.Event()
        )
        self.assertEqual(parallel.success, 4)
        self.assertEqual(len(list(self.output.glob("*.mp4"))), 4)

    def test_watermark_corner_mode(self) -> None:
        make_clip_with_audio(self.head / "h.mp4", "blue")
        make_clip_with_audio(self.tail / "t.mp4", "red")
        watermark = self.temp / "wm.png"
        Image.new("RGBA", (120, 60), (0, 255, 0, 160)).save(watermark)
        result = process_batch(
            self._config(
                count=1,
                use_watermark=True,
                watermark_path=str(watermark),
                watermark_mode="角落水印",
                watermark_position="右下角",
                watermark_scale=0.2,
                watermark_opacity=0.5,
            ),
            threading.Event(),
            threading.Event(),
        )
        self.assertEqual(result.success, 1)
        self.assertEqual(result.failed, 0)

    def test_thumbnail_generation(self) -> None:
        make_clip(self.head / "h.mp4", "blue")
        thumb = get_thumbnail(str(self.head / "h.mp4"), max_width=320)
        self.assertIsNotNone(thumb)
        self.assertTrue(Path(thumb).exists())
        self.assertEqual(Path(thumb).suffix, ".jpg")
        # 缓存命中：再次调用返回同一文件
        thumb2 = get_thumbnail(str(self.head / "h.mp4"), max_width=320)
        self.assertEqual(thumb, thumb2)

    def test_precheck_marks_bad_files(self) -> None:
        make_clip(self.head / "good.mp4", "blue")
        make_clip(self.tail / "good.mp4", "red")
        (self.tail / "bad.mp4").write_bytes(b"not a real video")
        report = precheck_materials(self._config())
        tails = {item["name"]: item for item in report["tail"]}
        self.assertTrue(tails["good.mp4"]["ok"])
        self.assertFalse(tails["bad.mp4"]["ok"])
        self.assertGreaterEqual(tails["good.mp4"]["duration"], 1.0)

    def test_concat_copy_produces_playable(self) -> None:
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        dst = self.temp / "joined.mp4"
        concat_copy([str(self.head / "h.mp4"), str(self.tail / "t.mp4")], str(dst), threading.Event(), threading.Event())
        info = probe_media(str(dst))
        self.assertTrue(info["has_video"])
        self.assertGreaterEqual(info["duration"], 3.0)

    def test_normalize_audio_flag_cache_isolation(self) -> None:
        make_clip_with_audio(self.head / "h.mp4", "blue")
        make_clip_with_audio(self.tail / "t.mp4", "red")
        norm_dir = cache_dir() / "norm"
        before = len(list(norm_dir.glob("*.mp4")))
        process_batch(
            self._config(count=1, normalize_audio=False),
            threading.Event(), threading.Event(),
        )
        process_batch(
            self._config(count=1, normalize_audio=True),
            threading.Event(), threading.Event(),
        )
        after = len(list(norm_dir.glob("*.mp4")))
        self.assertGreater(after, before, "统一响度开关应产生独立缓存项")


if __name__ == "__main__":
    unittest.main()
