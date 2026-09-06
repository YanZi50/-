import unittest

from platform_presets import apply_preset, get_presets


class PlatformPresetTests(unittest.TestCase):
    def test_presets(self) -> None:
        presets = get_presets()
        self.assertIn("抖音竖屏", presets)
        self.assertEqual(apply_preset("B站横屏")["resolution"], "1920x1080")


if __name__ == "__main__":
    unittest.main()
