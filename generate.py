"""Generate a daily RSS feed. State is kept separately from the published feed."""
import calendar
import datetime as dt
import email.utils
import html
import json
import logging
import os
import random
import re
from pathlib import Path
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import feedparser
import requests
import trafilatura

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state.json"
OUT = ROOT / "public"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
UTC = dt.timezone.utc
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "PersonalReadingRSS/1.0 (+RSS reader)"})


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def clean(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", value or "")).strip()


def published(entry):
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    return dt.datetime.fromtimestamp(calendar.timegm(stamp), UTC) if stamp else None


def collect(config, now):
    candidates = []
    for source in config["sources"]:
        if not source.get("allow_full_republish", False):
            continue
        try:
            response = SESSION.get(source["url"], timeout=25)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for item in feed.entries[:60]:
                when = published(item)
                link = item.get("link", "")
                if not when or not link.startswith(("https://", "http://")):
                    continue
                age = (now - when).total_seconds() / 86400
                if age < -1 or age > 90:
                    continue
                text = clean(item.get("title", "") + " " + item.get("summary", "")).lower()
                topics = [name for name, terms in config["topics"].items()
                          if any(term.lower() in text for term in terms)]
                candidates.append({"title": clean(item.get("title", "")), "link": link,
                                   "source": source["name"], "language": source["language"],
                                   "when": when, "age": max(age, 0), "topics": topics})
        except (requests.RequestException, ValueError) as exc:
            logging.warning("来源 %s 不可用: %s", source["name"], exc)
    return candidates


def ordered_candidates(candidates, category, topics, rng):
    pool = [c for c in candidates if category == "random" or c["topics"]]
    if category == "interest" and pool:
        available = [t for t in topics if any(t in c["topics"] for c in pool)]
        topic = rng.choice(available)
        pool = [c for c in pool if topic in c["topics"]]
    # Prefer recency while preserving random choice; 7d, then 30d, then 90d.
    result = []
    for low, high in ((-1, 7), (7, 30), (30, 90)):
        group = [c for c in pool if low < c["age"] <= high]
        while group:
            c = rng.choices(group, weights=[1 / (1 + x["age"] / 7) for x in group])[0]
            result.append(c)
            group.remove(c)
    return result


def translate(title, text):
    key = os.getenv("TRANSLATION_API_KEY")
    base = os.getenv("TRANSLATION_BASE_URL", "").rstrip("/")
    model = os.getenv("TRANSLATION_MODEL")
    if not key or not base or not model:
        raise ValueError("未设置翻译 API；英文文章不会发布")
    response = SESSION.post(base + "/chat/completions", timeout=120,
        headers={"Authorization": "Bearer " + key},
        json={"model": model, "temperature": 0.2, "messages": [
            {"role": "system", "content": "将用户提供的英文标题及文章完整忠实翻译成简体中文，不要总结或增删。只输出 JSON 对象，包含 title 和 body 两个字符串字段。保留段落换行。"},
            {"role": "user", "content": json.dumps({"title": title, "body": text}, ensure_ascii=False)}
        ]})
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content)
    data = json.loads(content)
    return data["title"], data["body"]


def get_article(candidate, minutes):
    response = SESSION.get(candidate["link"], timeout=30)
    response.raise_for_status()
    extracted = trafilatura.extract(response.text, url=candidate["link"], include_comments=False,
                                    include_tables=True, favor_precision=True)
    if not extracted:
        return None
    # Cheap length filter before paying for translation. English is ~200–250 words/minute.
    if candidate["language"] == "en":
        n = len(re.findall(r"\b[\w'-]+\b", extracted))
        bounds = (600, 1600) if minutes == 5 else (1300, 3100)
    else:
        n = len(re.findall(r"[\u3400-\u9fff]", extracted))
        bounds = (750, 1800) if minutes == 5 else (1500, 3600)
    if not bounds[0] <= n <= bounds[1]:
        return None
    title, body = candidate["title"], extracted
    if candidate["language"] == "en":
        title, body = translate(title, body)
        if len(re.findall(r"[\u3400-\u9fff]", body)) < bounds_for_chinese(minutes)[0]:
            return None
    chars = len(re.findall(r"[\u3400-\u9fff]", body))
    lo, hi = bounds_for_chinese(minutes)
    if not lo <= chars <= hi:
        return None
    return title, body


def bounds_for_chinese(minutes):
    return (750, 1800) if minutes == 5 else (1500, 3600)


def classic_item(classics, state, today, rng):
    available = [x for x in classics if x.get("enabled", True) and "暂不阅读" not in x.get("tags", [])
                 and "已读" not in x.get("tags", [])]
    if not available:
        return None
    unseen = [x for x in available if classic_id(x) not in state["classic_seen"]]
    if not unseen:
        logging.warning("古籍选段已读完；请添加选段或重置历史")
        return None
    books = sorted({x["book"] for x in unseen})
    book = rng.choice(books)
    choice = rng.choice([x for x in unseen if x["book"] == book])
    state["classic_seen"].append(classic_id(choice))
    return {"id": "classic:" + classic_id(choice), "day": today, "title": "古籍｜" + choice["book"] + "·" + choice["title"],
            "link": choice["source"], "body": "<p>" + html.escape(choice["text"]) + "</p><p>辅助阅读：" +
            html.escape(choice.get("note", "")) + "</p>", "timestamp": dt.datetime.now(UTC).isoformat()}


def classic_id(item):
    return item["book"] + "|" + item["title"] + "|" + item["text"]


def render(config, state, now):
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for key, value in (("title", config["title"]), ("link", os.getenv("FEED_SITE_URL", "https://github.com/")),
                       ("description", "每日随机文章与古籍选段"), ("language", "zh-CN")):
        ET.SubElement(channel, key).text = value
    cutoff = now.date() - dt.timedelta(days=config.get("days_retained", 30))
    for row in reversed(state["items"]):
        if dt.date.fromisoformat(row["day"]) < cutoff:
            continue
        item = ET.SubElement(channel, "item")
        for key, value in (("title", row["title"]), ("link", row["link"]), ("description", row["body"]),
                           ("guid", row["id"]), ("pubDate", email.utils.format_datetime(dt.datetime.fromisoformat(row["timestamp"])))):
            elem = ET.SubElement(item, key)
            elem.text = value
            if key == "guid":
                elem.set("isPermaLink", "false")
    OUT.mkdir(exist_ok=True)
    ET.ElementTree(rss).write(OUT / "feed.xml", encoding="utf-8", xml_declaration=True)


def main():
    config = load(ROOT / "config.json", {})
    classics = load(ROOT / "classics.json", [])
    state = load(STATE, {"items": [], "seen": [], "classic_seen": [], "plans": {}})
    now = dt.datetime.now(UTC)
    today = now.astimezone(ZoneInfo(config["timezone"])).date().isoformat()
    rng = random.SystemRandom()
    plan = state["plans"].setdefault(today, [("interest" if rng.random() < .7 else "random") for _ in range(3)])
    today_items = [x for x in state["items"] if x["day"] == today]
    if not any(x["id"].startswith("classic:") for x in today_items):
        classic = classic_item(classics, state, today, rng)
        if classic:
            state["items"].append(classic)
    candidates = collect(config, now) if any(not any(x["id"] == today + ":" + str(i) for x in today_items) for i in range(3)) else []
    for i, minutes in enumerate((5, 10, 10)):
        item_id = today + ":" + str(i)
        if any(x["id"] == item_id for x in state["items"]):
            continue
        available = [c for c in candidates if c["link"] not in state["seen"]]
        for candidate in ordered_candidates(available, plan[i], config["topics"], rng):
            try:
                result = get_article(candidate, minutes)
            except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
                logging.warning("文章处理失败 %s: %s", candidate["link"], exc)
                continue
            if not result:
                continue
            title, body = result
            paragraphs = "".join("<p>" + html.escape(p) + "</p>" for p in re.split(r"\n\s*\n", body) if p.strip())
            meta = f"{candidate['source']} · {candidate['when'].date().isoformat()} · 约{minutes}分钟 · " + ("兴趣领域" if plan[i] == "interest" else "随机发现")
            state["items"].append({"id": item_id, "day": today, "title": title, "link": candidate["link"],
                                   "body": "<p>" + html.escape(meta) + "</p>" + paragraphs,
                                   "timestamp": now.isoformat()})
            state["seen"].append(candidate["link"])
            break
        else:
            logging.warning("今天第 %s 篇暂时没有合适候选（%s 分钟，%s）", i + 1, minutes, plan[i])
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    render(config, state, now)
    logging.info("生成 %s", OUT / "feed.xml")


if __name__ == "__main__":
    main()
