import json
import tempfile
import unittest
from pathlib import Path

from config_store import load_config_file, save_config_file


class ConfigStoreTests(unittest.TestCase):
    def test_save_and_load(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sppj_config_"))
        path = root / "configs" / "last_config.json"
        payload = {
            "head_folder": "D:/head",
            "tail_folder": "D:/tail",
            "count": 10,
            "resolution": "1080x1920",
        }
        save_config_file(path, payload)
        loaded = load_config_file(path)
        self.assertEqual(loaded, payload)

    def test_load_missing_returns_empty(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sppj_config_"))
        self.assertEqual(load_config_file(root / "missing.json"), {})


if __name__ == "__main__":
    unittest.main()
