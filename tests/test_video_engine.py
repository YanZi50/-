# -*- coding: utf-8 -*-
"""video_engine 核心纯函数回归测试（不依赖真实 ffmpeg/素材文件）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_engine import (  # noqa: E402
    JobConfig,
    build_combinations,
    dedupe_delta,
    pick_middle_sequence,
    pick_transition,
    render_output_name,
    resolve_duration_mode,
    resolve_resolution,
    _vcodec_args,
)


class TestBuildCombinations(unittest.TestCase):
    def test_no_fixed_basic(self):
        heads = ["h1.mp4", "h2.mp4"]
        tails = ["t1.mp4", "t2.mp4"]
        combos = build_combinations(heads, tails, None, None, 4, seed=1)
        self.assertEqual(len(combos), 4)
        for h, t in combos:
            self.assertIn(h, heads)
            self.assertIn(t, tails)

    def test_no_fixed_dedupe_no_duplicate_pairs(self):
        heads = ["h1.mp4", "h2.mp4", "h3.mp4"]
        tails = ["t1.mp4", "t2.mp4", "t3.mp4"]
        combos = build_combinations(heads, tails, None, None, 100, seed=7)
        # 组合数不足时返回全部可用组合（3x3=9），不循环复用
        self.assertEqual(len(combos), 9)
        self.assertEqual(len(set(combos)), 9)

    def test_no_fixed_dedupe_disabled_returns_count(self):
        heads = ["h1.mp4", "h2.mp4"]
        tails = ["t1.mp4", "t2.mp4"]
        combos = build_combinations(heads, tails, None, None, 10, seed=3, dedupe_enabled=False)
        self.assertEqual(len(combos), 10)

    def test_fixed_head(self):
        heads = ["h1.mp4", "h2.mp4"]
        tails = ["t1.mp4", "t2.mp4", "t3.mp4"]
        combos = build_combinations(heads, tails, "h1.mp4", None, 2, seed=5)
        self.assertEqual(len(combos), 2)
        for h, t in combos:
            self.assertEqual(h, "h1.mp4")
            self.assertIn(t, tails)
        # 不足时不循环
        combos2 = build_combinations(heads, tails, "h1.mp4", None, 99, seed=5)
        self.assertEqual(len(combos2), 3)
        self.assertEqual(len(set(combos2)), 3)

    def test_fixed_tail(self):
        heads = ["h1.mp4", "h2.mp4", "h3.mp4"]
        tails = ["t1.mp4"]
        combos = build_combinations(heads, tails, None, "t1.mp4", 2, seed=5)
        self.assertEqual(len(combos), 2)
        for h, t in combos:
            self.assertEqual(t, "t1.mp4")
            self.assertIn(h, heads)

    def test_both_fixed(self):
        combos = build_combinations(["a.mp4", "b.mp4"], ["c.mp4", "d.mp4"], "a.mp4", "c.mp4", 5, seed=1)
        self.assertEqual(combos, [("a.mp4", "c.mp4")])

    def test_empty_lists(self):
        self.assertEqual(build_combinations([], ["t.mp4"], None, None, 3, seed=1), [])
        self.assertEqual(build_combinations(["h.mp4"], [], None, None, 3, seed=1), [])

    def test_deterministic_same_seed(self):
        heads, tails = ["h1.mp4", "h2.mp4", "h3.mp4"], ["t1.mp4", "t2.mp4", "t3.mp4"]
        a = build_combinations(heads, tails, None, None, 5, seed=42)
        b = build_combinations(heads, tails, None, None, 5, seed=42)
        self.assertEqual(a, b)


class TestDedupeDelta(unittest.TestCase):
    def test_off_returns_empty(self):
        self.assertEqual(dedupe_delta("off", {}, 1), {})
        self.assertEqual(dedupe_delta("invalid", {}, 1), {})

    def test_light_visual_fields(self):
        d = dedupe_delta("light", {"visual": True}, 10)
        self.assertTrue(d["visual"])
        self.assertIn("brightness", d)
        self.assertIn("contrast", d)
        self.assertIn("saturation", d)
        self.assertIn("zoom", d)
        self.assertGreaterEqual(d["zoom"], 1.0)
        self.assertLessEqual(d["zoom"], 1.03)

    def test_deep_has_random_transition(self):
        d = dedupe_delta("deep", {"visual": True, "segment": True, "audio": True}, 10)
        self.assertIn("random_transition", d)
        self.assertTrue(d["random_transition"])
        self.assertIn("transition", d)
        self.assertIn("transition_duration", d)
        self.assertIn("bgm_shift_seed", d)

    def test_deterministic_same_seed(self):
        opts = {"visual": True, "segment": True, "audio": True, "speed": True, "pitch": True}
        a = dedupe_delta("deep", opts, 123)
        b = dedupe_delta("deep", opts, 123)
        self.assertEqual(a, b)
        c = dedupe_delta("deep", opts, 124)
        self.assertNotEqual(a, c)

    def test_noise_range(self):
        d = dedupe_delta("light", {"noise": True}, 1)
        self.assertGreaterEqual(d["noise"], 3)
        self.assertLessEqual(d["noise"], 16)


class TestPickTransition(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(
            head_folder="h", tail_folder="t", transition_mode="不使用",
            transition_type="fade", transition_types=[], use_transition=False, random_seed=1,
        )
        base.update(kw)
        return JobConfig(**base)

    def test_fixed_mode(self):
        cfg = self._cfg(transition_mode="固定", transition_type="dissolve")
        self.assertEqual(pick_transition(cfg, 1), "dissolve")

    def test_random_mode_within_pool(self):
        pool = ["fade", "wipeleft", "zoomin"]
        cfg = self._cfg(transition_mode="随机", transition_types=pool)
        for i in range(20):
            self.assertIn(pick_transition(cfg, i), pool)

    def test_random_deterministic(self):
        cfg = self._cfg(transition_mode="随机")
        self.assertEqual(pick_transition(cfg, 3), pick_transition(cfg, 3))

    def test_use_transition_flag(self):
        cfg = self._cfg(use_transition=True, transition_type="slideleft")
        self.assertEqual(pick_transition(cfg, 1), "slideleft")

    def test_off_returns_none(self):
        self.assertIsNone(pick_transition(self._cfg(), 1))


class TestRenderOutputName(unittest.TestCase):
    def test_default_template(self):
        name = render_output_name("", 1, r"D:\h\开头A.mp4", r"D:\t\结尾B.mp4", None)
        self.assertEqual(name, "output_001_开头A_结尾B.mp4")

    def test_template_with_middle(self):
        name = render_output_name("{序号}_{开头}_{中间}_{结尾}", 7, "a.mp4", "b.mp4", "c.mp4")
        self.assertEqual(name, "007_a_c_b.mp4")

    def test_no_index_appends_index(self):
        name = render_output_name("我的视频", 2, "a.mp4", "b.mp4", None)
        self.assertTrue(name.startswith("我的视频_002"), name)

    def test_invalid_chars_cleaned(self):
        name = render_output_name("{开头}", 1, r"D:\x\a?b:c*.mp4", "t.mp4", None)
        self.assertNotIn("?", name)
        self.assertNotIn(":", name)
        self.assertNotIn("*", name)

    def test_index_always_3_digits(self):
        name = render_output_name("{序号}", 12, "a.mp4", "b.mp4", None)
        self.assertEqual(name, "012.mp4")


class TestResolvers(unittest.TestCase):
    def test_resolution(self):
        self.assertEqual(resolve_resolution("1080x1920"), (1080, 1920))
        # 实现：仅 1080x1920 为竖屏，其余取值一律横屏 1920x1080 兜底
        self.assertEqual(resolve_resolution("720x1280"), (1920, 1080))
        self.assertEqual(resolve_resolution(""), (1920, 1080))

    def test_duration_mode(self):
        self.assertEqual(resolve_duration_mode("15s"), 15.0)
        self.assertEqual(resolve_duration_mode("25s"), 25.0)
        self.assertEqual(resolve_duration_mode("30s"), 30.0)
        self.assertIsNone(resolve_duration_mode("不限制"))
        self.assertIsNone(resolve_duration_mode("啥也不是"))


class TestPickMiddleSequence(unittest.TestCase):
    def test_fixed_items_priority(self):
        pools = [{"folder": "m", "files": ["m1.mp4", "m2.mp4", "m3.mp4"], "items": ["m2.mp4"], "count": None}]
        seq = pick_middle_sequence(pools, 1, 1)
        self.assertEqual(seq, ["m2.mp4"])

    def test_random_pick_count(self):
        files = [f"m{i}.mp4" for i in range(1, 11)]
        pools = [{"folder": "m", "files": files, "items": [], "count": 3}]
        seq = pick_middle_sequence(pools, 1, 5)
        self.assertEqual(len(seq), 3)
        self.assertEqual(len(set(seq)), 3)

    def test_exclude_head_tail(self):
        files = ["a.mp4", "b.mp4", "c.mp4"]
        pools = [{"folder": "m", "files": files, "items": [], "count": 10}]
        seq = pick_middle_sequence(pools, 1, 1, exclude=["a.mp4", "b.mp4"])
        self.assertEqual(seq, ["c.mp4"])

    def test_empty_pool(self):
        self.assertEqual(pick_middle_sequence([], 1, 1), [])

    def test_deterministic(self):
        files = [f"m{i}.mp4" for i in range(1, 11)]
        pools = [{"folder": "m", "files": files, "items": [], "count": 3}]
        self.assertEqual(pick_middle_sequence(pools, 2, 9), pick_middle_sequence(pools, 2, 9))


class TestVcodecArgs(unittest.TestCase):
    def test_cpu(self):
        args = _vcodec_args("cpu", 18)
        self.assertEqual(args[:2], ["-c:v", "libx264"])
        self.assertIn("-crf", args)

    def test_nvenc(self):
        args = _vcodec_args("nvenc", 18)
        self.assertEqual(args[:2], ["-c:v", "h264_nvenc"])
        self.assertIn("-qp", args)


if __name__ == "__main__":
    unittest.main()
