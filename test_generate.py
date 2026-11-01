import datetime as dt
import importlib
import random
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
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
            {"book": "A", "title": "1", "text": "正文", "source": "https://example.org", "tags": [], "summary": "大意", "notes": [["词", "释义"]]},
            {"book": "B", "title": "2", "text": "正文", "source": "https://example.org", "tags": ["已读"], "summary": "大意", "notes": [["词", "释义"]]},
        ]
        state = {"classic_seen": []}
        item = g.classic_item(classics, state, "2026-11-01", random.Random(1))
        self.assertIn("A", item["title"])
        self.assertIn("词语解释", item["body"])
        self.assertIn("大意", item["body"])
        repeated = g.classic_item(classics, state, "2026-11-02", random.Random(1))
        self.assertNotEqual(item["id"], repeated["id"])
        self.assertIn("A", repeated["title"])

    def test_actual_classic_library_has_long_annotated_passages(self):
        classics = g.load(g.ROOT / "classics.json", [])
        self.assertGreaterEqual(len(classics), 3)
        for item in classics:
            self.assertGreaterEqual(len(item["text"]), 600)
            self.assertTrue(item["summary"])
            self.assertTrue(all(term in item["text"] for term, _ in item["notes"]))

    def test_interest_search_tries_other_matching_topics(self):
        candidates = [
            {"topics": ["A"], "age": 1, "link": "a"},
            {"topics": ["B"], "age": 1, "link": "b"},
            {"topics": [], "age": 1, "link": "c"},
        ]
        found = g.ordered_candidates(candidates, "interest", {"A": [], "B": []}, random.Random(1))
        self.assertEqual({c["link"] for c in found}, {"a", "b"})

    def test_run_fills_missing_slots_and_retries_without_duplicates(self):
        now = dt.datetime.now(dt.timezone.utc)
        today = now.astimezone(g.ZoneInfo("Asia/Shanghai")).date().isoformat()
        classics = g.load(g.ROOT / "classics.json", [])
        candidates = [
            {"topics": ["AI"] if i == 0 else [], "age": 1, "link": f"https://example.org/{i}",
             "title": f"Title {i}", "source": "Example", "language": "zh", "when": now}
            for i in range(3)
        ]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            (root / "config.json").write_text(g.json.dumps({"title": "Test", "timezone": "Asia/Shanghai",
                "topics": {"AI": []}, "sources": []}), encoding="utf-8")
            (root / "classics.json").write_text(g.json.dumps(classics, ensure_ascii=False), encoding="utf-8")
            state = {"items": [], "seen": [], "classic_seen": [], "plans": {today: ["interest"] * 3}}
            (root / "state.json").write_text(g.json.dumps(state), encoding="utf-8")
            with patch.object(g, "ROOT", root), patch.object(g, "STATE", root / "state.json"), \
                 patch.object(g, "OUT", root / "public"), patch.object(g, "collect", return_value=candidates), \
                 patch.object(g, "get_article", side_effect=lambda c, m: (c["title"], "正文")):
                g.main()
                g.main()
                saved = g.load(root / "state.json", {})
                self.assertEqual(len(saved["items"]), 4)
                self.assertEqual(len(saved["seen"]), 3)
                self.assertTrue(any("兴趣候选不足" in x["body"] for x in saved["items"]))

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
