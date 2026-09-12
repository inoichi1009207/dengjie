"""前端静态一致性:app.js 里 $("#id") 引用的每个 id 都必须存在于 index.html。
2026-09-12 实撞:删掉说明文字后 #daily-cap 没了,loadToday 在这一行抛错,整页后续绑定全失效。"""
import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(__file__), "..", "app", "static")


class StaticIds(unittest.TestCase):
    def test_every_referenced_id_exists(self):
        js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
        html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
        ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
        present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
        missing = sorted(ids - present)
        self.assertEqual(missing, [], f"app.js 引用了 index.html 里不存在的 id: {missing}")

    def test_data_go_targets_are_tabs(self):
        html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
        tabs = set(re.findall(r'id="tab-([a-z]+)"', html))
        for t in set(re.findall(r'data-(?:go|tab)="([a-z]+)"', html)):
            self.assertIn(t, tabs, f"data-go/data-tab 指向不存在的页: {t}")


if __name__ == "__main__":
    unittest.main()
