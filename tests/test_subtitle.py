import unittest

import subtitle_plugin


class SubtitlePluginTests(unittest.TestCase):
    def test_available_boolean(self) -> None:
        self.assertIsInstance(subtitle_plugin.available(), bool)

    def test_generate_raises_when_unavailable(self) -> None:
        if subtitle_plugin.available():
            self.skipTest("faster-whisper is installed")
        with self.assertRaises(subtitle_plugin.SubtitleUnavailableError):
            subtitle_plugin.generate_subtitles("head.mp4", "tail.mp4", 1.0, "out.srt")


if __name__ == "__main__":
    unittest.main()
