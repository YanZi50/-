import tempfile
import unittest
from pathlib import Path

from history_store import list_history, save_history


class HistoryStoreTests(unittest.TestCase):
    def test_save_and_list(self) -> None:
        import history_store

        old_dir = history_store.history_dir
        root = Path(tempfile.mkdtemp(prefix="sppj_history_"))
        history_store.history_dir = lambda: root / "history"
        try:
            save_history({"success": 1, "failed": 0})
            items = list_history()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["success"], 1)
        finally:
            history_store.history_dir = old_dir


if __name__ == "__main__":
    unittest.main()
