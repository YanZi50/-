import tempfile
import unittest
from pathlib import Path

import template_store


class TemplateStoreTests(unittest.TestCase):
    def test_save_list_load_delete(self) -> None:
        old = template_store.template_dir
        root = Path(tempfile.mkdtemp(prefix="sppj_templates_"))
        template_store.template_dir = lambda: root / "templates"
        try:
            template_store.save_template("测试模板", {"count": 10})
            self.assertIn("测试模板", template_store.list_templates())
            loaded = template_store.load_template("测试模板")
            self.assertEqual(loaded["count"], 10)
            self.assertTrue(template_store.delete_template("测试模板"))
            self.assertNotIn("测试模板", template_store.list_templates())
        finally:
            template_store.template_dir = old


if __name__ == "__main__":
    unittest.main()
