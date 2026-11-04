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
openai = types.ModuleType("openai")
openai.OpenAI = object
openai.APIError = type("APIError", (Exception,), {})
openai.AuthenticationError = type("AuthenticationError", (openai.APIError,), {})
sys.modules.setdefault("openai", openai)
g = importlib.import_module("generate")


class FeedTests(unittest.TestCase):
    def test_translate_uses_configured_base_url_with_sdk(self):
        from unittest.mock import Mock
        response = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(
            content='{"title":"标题","body":"译文"}'))])
        client = Mock()
        client.chat.completions.create.return_value = response
        with patch.dict(g.os.environ, {"TRANSLATION_API_KEY": "test", "TRANSLATION_BASE_URL": "https://api.deepseek.com",
                                      "TRANSLATION_MODEL": "deepseek-flash"}), \
             patch.object(g, "OpenAI", return_value=client) as factory:
            self.assertEqual(g.translate("title", "body"), ("标题", "译文"))
            self.assertEqual(factory.call_args.kwargs["base_url"], "https://api.deepseek.com")
            self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "deepseek-flash")

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
            {"book": "A", "title": "1", "text": "词正文。" * 125, "source": "https://example.org", "tags": [], "summary": "大意", "notes": [["词", "释义"]]},
            {"book": "B", "title": "2", "text": "正文", "source": "https://example.org", "tags": ["已读"], "summary": "大意", "notes": [["词", "释义"]]},
        ]
        state = {"classic_seen": [], "items": []}
        item = g.classic_item(classics, state, "2026-11-01", random.Random(1))
        self.assertIn("A", item["title"])
        self.assertIn("词语解释", item["body"])
        self.assertIn("大意", item["body"])
        repeated = g.classic_item(classics, state, "2026-11-02", random.Random(1))
        self.assertNotEqual(item["id"], repeated["id"])
        self.assertIn("A", repeated["title"])

    def test_actual_classic_library_has_annotated_chapters(self):
        classics = g.load(g.ROOT / "classics.json", [])
        self.assertTrue({"春秋", "汉书"}.issubset({item["book"] for item in classics}))
        for item in classics:
            self.assertTrue(item["text"].strip())
            self.assertTrue(item["notes"])
            self.assertTrue(item["summary"])
            self.assertTrue(all(term in item["text"] for term, _ in item["notes"]))

    def test_short_chapters_stay_whole_and_long_chapters_are_balanced(self):
        classics = g.load(g.ROOT / "classics.json", [])
        for chapter in classics:
            segments = g.classic_segments(chapter)
            self.assertTrue(segments)
            total = len(g.re.findall(r"[\u3400-\u9fff]", chapter["text"]))
            counts = [len(g.re.findall(r"[\u3400-\u9fff]", segment["text"])) for segment in segments]
            self.assertEqual(sum(counts), total)
            self.assertEqual(
                "".join(segment["text"] for segment in segments).replace("\n", ""),
                chapter["text"].replace("\n", ""),
            )
            if total <= 400:
                self.assertEqual(len(segments), 1, chapter["title"])
                self.assertEqual(segments[0]["text"], chapter["text"])
            else:
                self.assertGreater(len(segments), 1, chapter["title"])
                self.assertLessEqual(max(counts) - min(counts), 60, chapter["title"])
        yijing = [item for item in classics if item["book"] == "周易"]
        self.assertEqual([item["title"] for item in yijing], [
            "系辞上：第一章", "系辞上：第二章", "系辞上：第三章", "系辞上：第四章"])
        sushu = [item for item in classics if item["title"] in ("遵义章第五", "安礼章第六")]
        self.assertEqual(len(sushu), 2)
        self.assertEqual(sum(len(g.classic_segments(chapter)) for chapter in sushu), 3)

    def test_short_chapter_below_old_minimum_can_be_selected(self):
        chapter = {"book": "Test", "title": "short", "text": "善不積不足以成名。",
                   "notes": [["積", "积累。"]], "summary": "论积累。", "source": "https://example.org"}
        item = g.classic_item([chapter], {"items": [], "classic_seen": []}, "2026-11-03", random.Random(1))
        self.assertIsNotNone(item)
        self.assertIn(chapter["text"], item["body"])
        self.assertNotIn("（1/", item["title"])

    def test_long_chapter_can_have_three_parts_and_selection_stays_random(self):
        from unittest.mock import Mock
        chapter = {"book": "Test", "title": "long", "text": "學而時習之。" * 180,
                   "notes": [["習", "温习。"]], "summary": "论学习。", "source": "https://example.org"}
        self.assertEqual(len(g.classic_segments(chapter)), 3)
        rng = Mock()
        rng.choice.side_effect = lambda choices: choices[-1]
        item = g.classic_item([chapter], {"items": [], "classic_seen": []}, "2026-11-03", rng)
        self.assertIn("（3/3）", item["title"])

    def test_four_daily_classic_passages_are_short_and_do_not_repeat(self):
        classics = g.load(g.ROOT / "classics.json", [])
        segments = {s["segment_id"]: s for item in classics for s in g.classic_segments(item)}
        for segment in segments.values():
            count = len(g.re.findall(r"[\u3400-\u9fff]", segment["text"]))
            self.assertGreater(count, 0)
            self.assertLessEqual(count, 400)
        for seed in range(30):
            state = {"items": [], "classic_seen": []}
            for slot in range(4):
                item = g.classic_item(classics, state, "2026-11-02", random.Random(seed + slot))
                self.assertIsNotNone(item)
                self.assertEqual(len(item["classic_ids"]), 1)
                self.assertIn(item["classic_ids"][0], segments)
                previous = {key for row in state["items"] for key in row["classic_ids"]}
                self.assertFalse(previous.intersection(item["classic_ids"]))
                state["items"].append(item)

    def test_previous_full_passage_is_not_repeated_today(self):
        classics = g.load(g.ROOT / "classics.json", [])
        state = {"classic_seen": [], "items": [{"day": "2026-11-02", "classic_ids": [g.classic_id(classics[0])]}]}
        for seed in range(30):
            item = g.classic_item(classics, state, "2026-11-02", random.Random(seed))
            self.assertNotIn(item["classic_ids"][0], {x["segment_id"] for x in g.classic_segments(classics[0])})

    def test_classics_cycle_when_all_segments_used_today(self):
        classics = g.load(g.ROOT / "classics.json", [])[:1]
        state = {"classic_seen": [], "items": []}
        for _ in range(3):
            item = g.classic_item(classics, state, "2026-11-02", random.Random(1))
            self.assertIsNotNone(item)
            state["items"].append(item)
        self.assertEqual(len({x["id"] for x in state["items"]}), 3)
        self.assertNotEqual(state["items"][0]["classic_ids"], state["items"][1]["classic_ids"])

    def test_rate_limited_host_is_not_requested_again_in_run(self):
        from unittest.mock import Mock
        response = Mock(status_code=429)
        response.raise_for_status.side_effect = RuntimeError("429")
        session = Mock()
        session.get.return_value = response
        g.RATE_LIMITED_HOSTS.clear()
        with patch.object(g, "SESSION", session):
            with self.assertRaises(RuntimeError):
                g.fetch_url("https://example.com/a", 1)
            with self.assertRaises(g.HostRateLimited):
                g.fetch_url("https://example.com/b", 1)
        self.assertEqual(session.get.call_count, 1)

    def test_invalid_translation_key_skips_further_english_but_saves_classic(self):
        now = dt.datetime(2026, 11, 4, 2, tzinfo=dt.timezone.utc)
        candidates = [{"topics": [], "age": 1, "link": f"https://example.org/{i}",
                       "title": "Title", "source": "Example", "language": "en", "when": now}
                      for i in range(3)]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            (root / "config.json").write_text(g.json.dumps({"title": "Test", "timezone": "Asia/Shanghai",
                "topics": {}, "sources": []}), encoding="utf-8")
            (root / "classics.json").write_text((g.ROOT / "classics.json").read_text(encoding="utf-8"), encoding="utf-8")
            with patch.dict(g.os.environ, {"READING_SCHEDULE": ""}), patch.object(g, "ROOT", root), patch.object(g, "STATE", root / "state.json"), \
                 patch.object(g, "OUT", root / "public"), patch.object(g, "collect", return_value=candidates), \
                 patch.object(g, "get_article", side_effect=g.AuthenticationError("invalid")) as get_article:
                g.main(now=now)
                self.assertEqual(get_article.call_count, 1)
                self.assertEqual(len(g.load(root / "state.json", {})["items"]), 1)
                self.assertTrue((root / "public" / "feed.xml").exists())
                # 修复翻译后只补文章，不能重复生成已成功的古文。
                with patch.object(g, "get_article", return_value=("译文标题", "译文正文")):
                    g.main(now=now)
                saved = g.load(root / "state.json", {})
                self.assertEqual(len(saved["items"]), 2)
                self.assertEqual(sum(item["id"].startswith("classic:") for item in saved["items"]), 1)

    def test_interest_search_tries_other_matching_topics(self):
        candidates = [
            {"topics": ["A"], "age": 1, "link": "a"},
            {"topics": ["B"], "age": 1, "link": "b"},
            {"topics": [], "age": 1, "link": "c"},
        ]
        found = g.ordered_candidates(candidates, "interest", {"A": [], "B": []}, random.Random(1))
        self.assertEqual({c["link"] for c in found}, {"a", "b"})

    def test_four_slots_generate_one_pair_each_and_reruns_do_not_duplicate(self):
        today = "2026-11-04"
        zone = g.ZoneInfo("Asia/Shanghai")
        now = dt.datetime(2026, 11, 4, 5, tzinfo=zone)
        classics = g.load(g.ROOT / "classics.json", [])
        candidates = [
            {"topics": [], "age": 1, "link": f"https://example.org/{i}",
             "title": f"Title {i}", "source": "Example", "language": "zh", "when": now}
            for i in range(8)
        ]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            (root / "config.json").write_text(g.json.dumps({"title": "Test", "timezone": "Asia/Shanghai",
                "topics": {"AI": []}, "sources": []}), encoding="utf-8")
            (root / "classics.json").write_text(g.json.dumps(classics, ensure_ascii=False), encoding="utf-8")
            # 保留旧版每日三个文章位置的计划，不能让它阻止新增四个时段。
            state = {"items": [], "seen": [], "classic_seen": [], "plans": {today: ["interest"] * 3},
                     "slot_plans": {f"{today}:{hour:02d}:00": "interest" for hour in (5, 10, 15, 20)}}
            (root / "state.json").write_text(g.json.dumps(state), encoding="utf-8")
            with patch.dict(g.os.environ, {"READING_SCHEDULE": ""}), \
                 patch.object(g, "ROOT", root), patch.object(g, "STATE", root / "state.json"), \
                 patch.object(g, "OUT", root / "public"), patch.object(g, "collect", return_value=candidates) as collect, \
                 patch.object(g, "get_article", side_effect=lambda c, m: (c["title"], "正文")) as article:
                g.main(now=now.replace(hour=1, minute=50))
                self.assertEqual(g.load(root / "state.json", {})["items"], [])
                collect.assert_not_called()
                for count, hour in enumerate((5, 10, 15, 20), 1):
                    instant = now.replace(hour=hour)
                    with self.assertLogs(level="INFO") as logs:
                        g.main(now=instant)
                    self.assertTrue(any("本次新增 1 篇古文、1 篇文章" in line for line in logs.output))
                    saved = g.load(root / "state.json", {})
                    self.assertEqual(len(saved["items"]), count * 2)
                    current = [item for item in saved["items"] if item["slot"] == f"{hour:02d}:00"]
                    self.assertEqual(len(current), 2)
                    self.assertEqual(sum(item["id"].startswith("classic:") for item in current), 1)
                    # 同一时段重复运行，正文、GUID、阅读历史和抽签结果都应稳定。
                    g.main(now=instant + dt.timedelta(minutes=10))
                    self.assertEqual(g.load(root / "state.json", {}), saved)
                self.assertEqual(collect.call_count, 4)
                self.assertEqual([call.args[1] for call in article.call_args_list], [5, 10, 10, 10])
                self.assertEqual(len(saved["seen"]), 4)
                self.assertEqual(len(set(saved["seen"])), 4)
                self.assertTrue(any("兴趣候选不足" in item["body"] for item in saved["items"]))
                self.assertEqual(saved["plans"], state["plans"])
                g.main(now=now + dt.timedelta(days=1))
                newest = g.load(root / "state.json", {})["items"][-2:]
                self.assertTrue(all(item["day"] == "2026-11-05" and item["slot"] == "05:00" for item in newest))
                self.assertFalse({item["id"] for item in newest}.intersection(item["id"] for item in saved["items"]))

    def test_only_current_slot_is_filled_and_retry_handles_missing_classic(self):
        now = dt.datetime(2026, 11, 4, 15, 5, tzinfo=g.ZoneInfo("Asia/Shanghai"))
        classics = g.load(g.ROOT / "classics.json", [])
        candidate = {"topics": [], "age": 1, "link": "https://example.org/a", "title": "Title",
                     "source": "Example", "language": "zh", "when": now}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            (root / "config.json").write_text(g.json.dumps({"title": "Test", "timezone": "Asia/Shanghai",
                "topics": {}, "sources": []}), encoding="utf-8")
            (root / "classics.json").write_text("[]", encoding="utf-8")
            with patch.dict(g.os.environ, {"READING_SCHEDULE": ""}), \
                 patch.object(g, "ROOT", root), patch.object(g, "STATE", root / "state.json"), \
                 patch.object(g, "OUT", root / "public"), patch.object(g, "collect", return_value=[candidate]) as collect, \
                 patch.object(g, "get_article", return_value=("Title", "正文")):
                g.main(now=now)
                saved = g.load(root / "state.json", {})
                self.assertEqual([row["id"] for row in saved["items"]], ["article:2026-11-04:15:00"])
                self.assertEqual(list(saved["slot_plans"]), ["2026-11-04:15:00"])
                (root / "classics.json").write_text(g.json.dumps(classics, ensure_ascii=False), encoding="utf-8")
                g.main(now=now)
                saved = g.load(root / "state.json", {})
                self.assertEqual(len(saved["items"]), 2)
                self.assertTrue(all(row["slot"] == "15:00" for row in saved["items"]))
                self.assertEqual(collect.call_count, 1)

    def test_schedule_timezone_boundaries_and_delayed_jobs(self):
        zone = g.ZoneInfo("Asia/Shanghai")
        for hour in (5, 10, 15, 20):
            instant = dt.datetime(2026, 11, 4, hour, tzinfo=zone)
            slot = g.reading_slot(instant.astimezone(dt.timezone.utc), "Asia/Shanghai")
            self.assertEqual(slot[:2], ("2026-11-04", f"{hour:02d}:00"))
            before = g.reading_slot(instant - dt.timedelta(seconds=1), "Asia/Shanghai")
            if hour == 5:
                self.assertIsNone(before)
            else:
                self.assertEqual(before[1], f"{hour - 5:02d}:00")
        # 05 点定时任务延迟到 10 点之后，仍只认领 05 点，不抢占 10 点。
        self.assertEqual(g.reading_slot(dt.datetime(2026, 11, 4, 10, 30, tzinfo=zone), "Asia/Shanghai", "0 21 * * *")[:2],
                         ("2026-11-04", "05:00"))
        self.assertEqual(g.reading_slot(dt.datetime(2026, 11, 5, 0, 30, tzinfo=zone), "Asia/Shanghai", "0 12 * * *")[:2],
                         ("2026-11-04", "20:00"))
        self.assertIsNone(g.reading_slot(dt.datetime(2026, 11, 5, 1, 50, tzinfo=zone), "Asia/Shanghai"))
        with self.assertRaises(ValueError):
            g.reading_slot(dt.datetime(2026, 11, 4, 10, tzinfo=zone), "Asia/Shanghai", "invalid")

    def test_workflow_crons_match_the_four_beijing_slots(self):
        workflow = (g.ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        crons = g.re.findall(r"cron: '([^']+)'", workflow)
        self.assertEqual(len(crons), 4)
        converted = []
        for cron in crons:
            minute, hour, day, month, weekday = cron.split()
            self.assertEqual((minute, day, month, weekday), ("0", "*", "*", "*"))
            utc = dt.datetime(2026, 11, 4, int(hour), tzinfo=dt.timezone.utc)
            local = utc.astimezone(g.ZoneInfo("Asia/Shanghai"))
            converted.append(local.hour)
            slot = g.reading_slot(utc, "Asia/Shanghai", cron)
            self.assertEqual(slot[:2], (local.date().isoformat(), f"{local.hour:02d}:00"))
        self.assertEqual(sorted(converted), [5, 10, 15, 20])
        self.assertIn("READING_SCHEDULE: ${{ github.event.schedule }}", workflow)

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

    def test_classic_parts_have_distinct_reading_pages_with_only_their_own_body(self):
        import xml.etree.ElementTree as ET
        now = dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc)
        source = "https://example.org/original-chapter"
        rows = [
            {"id": "classic:part-one", "day": "2026-11-03", "title": "古籍｜测试（1/2）",
             "link": source, "body": '<h2>第一段</h2><p>只有第一段的原文。</p><p><a href="' + source + '">原文出处</a></p>',
             "timestamp": now.isoformat()},
            {"id": "classic:part-two", "day": "2026-11-03", "title": "古籍｜测试（2/2）",
             "link": source, "body": '<h2>第二段</h2><p>只有第二段的原文。</p><p><a href="' + source + '">原文出处</a></p>',
             "timestamp": now.isoformat()},
        ]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            config = {"title": "阅读", "site_url": "https://reader.example/project/"}
            with patch.object(g, "OUT", root), patch.dict(g.os.environ, {"FEED_SITE_URL": ""}):
                g.render(config, {"items": rows}, now)
                items = ET.parse(root / "feed.xml").findall("./channel/item")
                links = [item.findtext("link") for item in items]
                self.assertEqual(len(set(links)), 2)
                expected_bodies = {row["id"]: row["body"] for row in rows}
                for item in items:
                    link = item.findtext("link")
                    self.assertTrue(link.startswith("https://reader.example/project/classics/"))
                    body = expected_bodies[item.findtext("guid")]
                    page = (root / "classics" / link.rsplit("/", 1)[1]).read_text(encoding="utf-8")
                    self.assertIn(body, page)
                    self.assertEqual(item.findtext("description"), body)
                    self.assertEqual(item.findtext("{" + g.CONTENT_NAMESPACE + "}encoded"), body)
                    other_body = next(value for value in expected_bodies.values() if value != body)
                    self.assertNotIn(other_body, page)
                # 重新渲染不改变条目身份、链接，也不覆写保存的原文地址。
                g.render(config, {"items": rows}, now)
                self.assertEqual([item.findtext("link") for item in ET.parse(root / "feed.xml").findall("./channel/item")], links)
                self.assertTrue(all(row["link"] == source for row in rows))
                with patch.dict(g.os.environ, {"FEED_SITE_URL": "https://custom.example/read/"}):
                    g.render(config, {"items": rows}, now)
                self.assertTrue(all(item.findtext("link").startswith("https://custom.example/read/classics/")
                                    for item in ET.parse(root / "feed.xml").findall("./channel/item")))


if __name__ == "__main__":
    unittest.main()
