import datetime as dt
import importlib
import random
import sys
import tempfile
import types
import unittest
from pathlib import Path

# Keep selection and RSS tests runnable without network or third-party packages.
requests = types.ModuleType("requests")
requests.Session = lambda: types.SimpleNamespace(headers={})
requests.RequestException = Exception
sys.modules.setdefault("requests", requests)
sys.modules.setdefault("feedparser", types.ModuleType("feedparser"))
sys.modules.setdefault("trafilatura", types.ModuleType("trafilatura"))
g = importlib.import_module("generate")


class FeedTests(unittest.TestCase):
    def test_interest_and_random_pools(self):
        candidates = [
            {"topics": ["AI"], "age": 2, "link": "a"},
            {"topics": [], "age": 1, "link": "b"},
            {"topics": ["AI"], "age": 8, "link": "c"},
        ]
        interest = g.ordered_candidates(candidates, "interest", {"AI": []}, random.Random(1))
        self.assertEqual([x["link"] for x in interest], ["a", "c"])
        discovery = g.ordered_candidates(candidates, "random", {"AI": []}, random.Random(1))
        self.assertEqual(set(x["link"] for x in discovery), {"a", "b", "c"})
        self.assertEqual(discovery[-1]["link"], "c")

    def test_classics_skip_excluded_and_record_history(self):
        classics = [
            {"book": "A", "title": "1", "text": "正文", "source": "https://example.org", "tags": []},
            {"book": "B", "title": "2", "text": "正文", "source": "https://example.org", "tags": ["已读"]},
        ]
        state = {"classic_seen": []}
        item = g.classic_item(classics, state, "2026-11-01", random.Random(1))
        self.assertIn("A", item["title"])
        self.assertIsNone(g.classic_item(classics, state, "2026-11-02", random.Random(1)))

    def test_rss_escapes_and_drops_old_entries(self):
        import xml.etree.ElementTree as ET
        now = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
        state = {"items": [
            {"day": "2026-01-01", "title": "old", "link": "https://example.org/old",
             "body": "<p>old</p>", "id": "old", "timestamp": now.isoformat()},
            {"day": "2026-11-01", "title": "中文 & title", "link": "https://example.org/new",
             "body": "<p>中文 &amp; text</p>", "id": "new", "timestamp": now.isoformat()},
        ]}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            previous = g.OUT
            try:
                g.OUT = Path(folder)
                g.render({"title": "阅读", "days_retained": 30}, state, now)
                root = ET.parse(g.OUT / "feed.xml").getroot()
                items = root.findall("./channel/item")
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].findtext("title"), "中文 & title")
                self.assertEqual(items[0].findtext("guid"), "new")
            finally:
                g.OUT = previous


if __name__ == "__main__":
    unittest.main()
