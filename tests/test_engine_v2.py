import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image
from imageio_ffmpeg import get_ffmpeg_exe

from video_engine import (
    JobConfig,
    build_middle_pools,
    cache_dir,
    concat_copy,
    get_thumbnail,
    normalize_clip,
    precheck_materials,
    process_batch,
    probe_media,
)
from tests.test_core import make_clip, make_clip_with_audio

FFMPEG = get_ffmpeg_exe()


def make_clip_with_audio_delayed(
    path: Path, color: str = "blue", duration: float = 2.0, delay: float = 0.5
) -> None:
    """构造音画不同步素材（保留供扩展验证）：音频内容整体后移 delay 秒。"""
    make_clip_offset_audio(path, color, duration, delay)


def make_clip_offset_audio(
    path: Path, color: str = "blue", duration: float = 2.0, delay: float = 0.5
) -> None:
    """构造音画不同步素材：音频内容整体后移 delay 秒（音频 PTS 起点非 0）。"""
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
            "-filter_complex",
            f"[1:a]asetpts=PTS+{delay}/TB[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
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

    def test_middle_multi_items_fixed_sequence(self) -> None:
        """固定勾选多条中间素材：按勾选顺序插入头尾之间。"""
        mid = self.temp / "middle"
        mid.mkdir()
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        make_clip(mid / "m1.mp4", "green")
        make_clip(mid / "m2.mp4", "yellow")
        middle_paths = [str(mid / "m1.mp4"), str(mid / "m2.mp4")]
        logs: list[str] = []
        result = process_batch(
            self._config(count=1, middle_folder=str(mid), middle_items=middle_paths),
            threading.Event(), threading.Event(), log=logs.append,
        )
        self.assertEqual(result.success, 1)
        self.assertEqual(result.failed, 0)
        item = result.success_items[0]
        self.assertEqual(item["middle_files"], middle_paths)
        self.assertIn("m1.mp4、m2.mp4", item["middle"])
        out = Path(item["output"])
        info = probe_media(str(out))
        self.assertTrue(info["has_video"])
        # 头+2中间+尾 = 4 个 1.5s 片段 ≈ 6s（允许转场缩短/编码误差）
        self.assertGreaterEqual(info["duration"], 5.0)
        gen_log = "\n".join(logs)
        self.assertIn("m1.mp4", gen_log)

    def test_middle_random_count(self) -> None:
        """未勾选固定中间时，按 middle_count 随机抽取多条。"""
        mid = self.temp / "middle"
        mid.mkdir()
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        for i in range(3):
            make_clip(mid / f"m{i}.mp4", "green")
        result = process_batch(
            self._config(count=2, middle_folder=str(mid), middle_count=2),
            threading.Event(), threading.Event(),
        )
        self.assertEqual(result.success, 2)
        for item in result.success_items:
            self.assertEqual(len(item.get("middle_files") or []), 2, "每条应插入 2 个中间片段")
            out = Path(item["output"])
            info = probe_media(str(out))
            self.assertGreaterEqual(info["duration"], 5.0)

    def test_middle_items_missing_raises(self) -> None:
        mid = self.temp / "middle"
        mid.mkdir()
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        make_clip(mid / "m1.mp4", "green")
        with self.assertRaises(Exception):
            process_batch(
                self._config(count=1, middle_folder=str(mid), middle_items=[str(mid / "ghost.mp4")]),
                threading.Event(), threading.Event(),
            )

    def test_transition_extended_types_playable(self) -> None:
        """新增转场类型（smoothleft/diagtl/zoomin/pixelize/circlecrop）可正常编码。"""
        make_clip_with_audio(self.head / "h.mp4", "blue")
        make_clip_with_audio(self.tail / "t.mp4", "red")
        for ttype in ["smoothleft", "diagtl", "zoomin", "pixelize", "circlecrop"]:
            out_dir = self.temp / ("out_" + ttype)
            out_dir.mkdir()
            result = process_batch(
                self._config(
                    count=1, output_folder=str(out_dir),
                    transition_mode="固定", transition_type=ttype, transition_duration=0.4,
                ),
                threading.Event(), threading.Event(),
            )
            self.assertEqual(result.success, 1, f"转场 {ttype} 应成功")
            info = probe_media(str(Path(result.success_items[0]["output"])))
            self.assertTrue(info["has_video"])

    def test_middle_with_transition(self) -> None:
        """多条中间 + 转场：链式 xfade 拼接可播放。"""
        mid = self.temp / "middle"
        mid.mkdir()
        make_clip_with_audio(self.head / "h.mp4", "blue")
        make_clip_with_audio(self.tail / "t.mp4", "red")
        make_clip_with_audio(mid / "m1.mp4", "green")
        make_clip_with_audio(mid / "m2.mp4", "yellow")
        result = process_batch(
            self._config(
                count=1,
                middle_folder=str(mid),
                middle_items=[str(mid / "m1.mp4"), str(mid / "m2.mp4")],
                transition_mode="固定", transition_type="fade", transition_duration=0.4,
            ),
            threading.Event(), threading.Event(),
        )
        self.assertEqual(result.success, 1)
        info = probe_media(str(Path(result.success_items[0]["output"])))
        self.assertTrue(info["has_video"])

    def test_middle_pools_sequence_order(self) -> None:
        """多池按池顺序插入：池1 固定勾选 2 条 + 池2 随机抽 1 条 → 3 段中间。"""
        mid1 = self.temp / "mid1"
        mid2 = self.temp / "mid2"
        mid1.mkdir()
        mid2.mkdir()
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        make_clip(mid1 / "a.mp4", "green")
        make_clip(mid1 / "b.mp4", "cyan")
        make_clip(mid2 / "x.mp4", "yellow")
        make_clip(mid2 / "y.mp4", "white")
        pools = [
            {"folder": str(mid1), "items": [str(mid1 / "a.mp4"), str(mid1 / "b.mp4")], "count": None},
            {"folder": str(mid2), "items": [], "count": 1},
        ]
        result = process_batch(
            self._config(count=1, middle_pools=pools, middle_folder=str(mid1)),
            threading.Event(), threading.Event(),
        )
        self.assertEqual(result.success, 1)
        mids = result.success_items[0]["middle_files"]
        self.assertEqual(len(mids), 3, "两条固定 + 一条随机 = 3 段中间")
        self.assertEqual(Path(mids[0]).name, "a.mp4")
        self.assertEqual(Path(mids[1]).name, "b.mp4")
        self.assertIn(Path(mids[2]).name, {"x.mp4", "y.mp4"})

    def test_build_middle_pools_fallback_single(self) -> None:
        """无 middle_pools 时回退旧单池字段。"""
        mid = self.temp / "middle"
        mid.mkdir()
        make_clip(mid / "m.mp4", "green")
        cfg = self._config(
            middle_folder=str(mid), middle_items=[str(mid / "m.mp4")], middle_count=2
        )
        pools = build_middle_pools(cfg)
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["items"], [str(mid / "m.mp4")])
        self.assertEqual(pools[0]["count"], 2)

    def test_middle_pools_item_missing_raises(self) -> None:
        """多池中某池固定素材缺失应报错。"""
        mid1 = self.temp / "mid1"
        mid2 = self.temp / "mid2"
        mid1.mkdir()
        mid2.mkdir()
        make_clip(self.head / "h.mp4", "blue")
        make_clip(self.tail / "t.mp4", "red")
        pools = [
            {"folder": str(mid1), "items": [str(mid1 / "ghost.mp4")], "count": None},
            {"folder": str(mid2), "items": [], "count": 1},
        ]
        with self.assertRaises(Exception):
            process_batch(
                self._config(count=1, middle_pools=pools),
                threading.Event(), threading.Event(),
            )

    def test_normalize_keeps_av_present(self) -> None:
        """归一化不破坏音画：正常同步素材归一化后音视频齐全、时长一致（-shortest 同步截断）。"""
        src = self.temp / "src.mp4"
        make_clip_with_audio(src, "blue", 2)
        dst = str(self.temp / "norm.mp4")
        before = probe_media(str(src))
        normalize_clip(
            src, dst, 1080, 1920, before["duration"], before["has_audio"],
            threading.Event(), threading.Event(),
        )
        info = probe_media(dst)
        self.assertTrue(info["has_video"] and info["has_audio"])
        self.assertLess(abs(info["duration"] - 2.0), 0.25, "归一化后时长应保持")

    def test_concat_av_sync_duration(self) -> None:
        """转场链音画同步的核心：xfade/acrossfade 的过渡点都基于归一化后真实时长，
        成片时长应精确等于 sum(真实时长) - 转场重叠（偏差 < 0.15s）。"""
        make_clip_with_audio(self.head / "h.mp4", "blue", 2)
        make_clip_with_audio(self.tail / "t.mp4", "red", 2)
        mid = self.temp / "middle"
        mid.mkdir()
        make_clip_with_audio(mid / "m.mp4", "green", 2)
        result = process_batch(
            self._config(
                count=1,
                middle_folder=str(mid),
                middle_items=[str(mid / "m.mp4")],
                transition_mode="固定", transition_type="fade", transition_duration=0.4,
            ),
            threading.Event(), threading.Event(),
        )
        self.assertEqual(result.success, 1)
        info = probe_media(str(Path(result.success_items[0]["output"])))
        self.assertTrue(info["has_video"] and info["has_audio"])
        expected = 2.0 * 3 - 0.4 * 2  # 三段各 2s，两个 0.4s 转场重叠
        self.assertLess(abs(info["duration"] - expected), 0.15,
                        "成片时长应精确等于标称拼接时长（音视频过渡点一致）")

    def test_concat_chain_four_segments_duration(self) -> None:
        """4 段链式转场回归：时长应精确 = sum(真实时长) - 3×转场。
        修复前 offset 递推少减转场重叠，offset 超出输入时长被 ffmpeg 截断（成片变短）。"""
        make_clip_with_audio(self.head / "h.mp4", "blue", 2)
        make_clip_with_audio(self.tail / "t.mp4", "red", 2)
        mid = self.temp / "middle"
        mid.mkdir()
        make_clip_with_audio(mid / "m1.mp4", "green", 2)
        make_clip_with_audio(mid / "m2.mp4", "yellow", 2)
        result = process_batch(
            self._config(
                count=1,
                middle_folder=str(mid),
                middle_items=[str(mid / "m1.mp4"), str(mid / "m2.mp4")],
                transition_mode="固定", transition_type="fade", transition_duration=0.5,
            ),
            threading.Event(), threading.Event(),
        )
        self.assertEqual(result.success, 1)
        info = probe_media(str(Path(result.success_items[0]["output"])))
        expected = 2.0 * 4 - 0.5 * 3
        self.assertLess(abs(info["duration"] - expected), 0.2,
                        f"4 段成片时长应为 {expected}s，实际 {info['duration']}s（offset 递推错误会截断成片）")

    def test_concat_copy_keeps_av_sync(self) -> None:
        """无转场流复制路径：归一化对齐后，流复制拼接应保持音画同步。"""
        make_clip_with_audio(self.head / "h.mp4", "blue", 2)
        make_clip_with_audio(self.tail / "t.mp4", "red", 2)
        result = process_batch(
            self._config(count=1, transition_mode="不使用"),
            threading.Event(), threading.Event(),
        )
        self.assertEqual(result.success, 1)
        out = str(Path(result.success_items[0]["output"]))
        info = probe_media(out)
        self.assertTrue(info["has_video"] and info["has_audio"])
        self.assertLess(abs(info["duration"] - 4.0), 0.25, "无转场成片时长应为两段之和")


if __name__ == "__main__":
    unittest.main()
