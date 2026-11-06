"""Prepare a Japanese episode from editorially reviewed RSS articles."""

import argparse
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen
import wave
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
RULES = tomllib.loads((ROOT / "tools/article_rules.toml").read_text(encoding="utf-8"))
SLOTS = RULES["slots"]
USER_AGENT = "ClearRockRadio/1.0"
VOICE = "http://localhost:50021"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request(url, payload=None, timeout=30):
    headers = {"User-Agent": USER_AGENT}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data, headers), timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}: {url}")
        return response.read()


def https_url(value):
    if not isinstance(value, str) or any(ch.isspace() for ch in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == "https" and bool(url.hostname) and url.username is None
    except ValueError:
        return False


def article_key(url):
    parts = urlsplit(url)
    query = tuple(sorted((name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
                         if not name.lower().startswith("utm_") and name.lower() not in {"fbclid", "gclid"}))
    return parts.netloc.lower(), parts.path.rstrip("/"), query


def aired_article_keys():
    urls = set()
    for path in sorted((ROOT / "episodes").glob("*.json")):
        for news in read_json(path).get("news", []):
            for source in (news, news.get("link") or {}, news.get("source_link") or {}):
                url = source.get("url")
                if https_url(url):
                    urls.add(article_key(url))
    return urls


def candidate_articles(now, *, include_excluded=False):
    articles = {}
    aired = aired_article_keys()
    feeds = {}
    cutoff = now - timedelta(days=RULES["days"])
    for category, slot in SLOTS.items():
        feed = slot["feed"]
        if feed not in feeds:
            feeds[feed] = ET.fromstring(request(feed))
        for item in feeds[feed].iterfind(".//{*}item"):
            title = item.findtext("{*}title", "").strip()
            link = item.findtext("{*}link", "").strip()
            stamp = item.findtext("{*}pubDate") or item.findtext("{*}date")
            if not title or not https_url(link) or not stamp:
                continue
            try:
                published = datetime.fromisoformat(stamp) if re.match(r"^\d{4}-", stamp) else parsedate_to_datetime(stamp)
            except ValueError:
                continue
            if published.tzinfo is None or not cutoff <= published <= now:
                continue
            if urlsplit(link).hostname != urlsplit(feed).hostname:
                continue
            required_hint = slot.get("required_hint")
            if required_hint and not re.search(required_hint, title, re.I):
                continue
            key = article_key(link)
            headline = f"{title} {urlsplit(link).path}"
            hints = {**RULES["exclude_hints"], **slot["exclude_hints"]}
            exclusion = "放送済みURL" if key in aired else next(
                (reason for reason, pattern in hints.items() if re.search(pattern, headline, re.I)), None
            )
            if exclusion and not include_excluded:
                continue
            article = {
                "category": category, "site": slot["site"], "title": title, "url": link,
                "published": published.astimezone(JST).isoformat(),
                "priority_hint": not exclusion and bool(re.search(slot["priority_hint"], title, re.I)),
            }
            if exclusion:
                article["excluded_reason"] = exclusion
            articles[category, key] = article
    ordered = sorted(articles.values(), key=lambda article: (
        list(SLOTS).index(article["category"]), "excluded_reason" in article,
        not article["priority_hint"], -datetime.fromisoformat(article["published"]).timestamp(), article["url"],
    ))
    return {(article["category"], article_key(article["url"])): article for article in ordered}


def select_articles(review, candidates):
    if not isinstance(review, list):
        raise ValueError("元記事を確認した記事の一覧を指定してください")
    selected, omitted, used = {}, {}, set()
    for entry in review:
        if not isinstance(entry, dict):
            raise ValueError("各項目に記事URLと確認済みの事実、または省略する枠と理由を指定してください")
        url = entry.get("url")
        reason = entry.get("reason")
        category = entry.get("category")
        key = None
        if "url" in entry:
            if not https_url(url):
                raise ValueError(f"出典URLが不正です: {url}")
            key = article_key(url)
            slots = [name for name in SLOTS if (name, key) in candidates]
            if category is None and len(slots) == 1:
                category = slots[0]
            if not slots:
                raise ValueError(f"直近{RULES['days']}日間の適格なRSS候補にありません: {url}")
            if category not in slots:
                raise ValueError(f"出典URLの採用枠を指定または見直してください: {url}")
            if key in used:
                raise ValueError(f"同じ出典URLを複数の枠に使えません: {url}")
        if category not in SLOTS or category in selected or category in omitted:
            raise ValueError(f"枠が不明または重複しています: {url or category}")
        if "url" not in entry:
            if not is_text(reason):
                raise ValueError(f"省略理由を指定してください: {category}")
            omitted[category] = reason
            continue
        article = candidates[category, key]
        used.add(key)
        facts = entry.get("facts")
        if not isinstance(facts, list) or not facts or not all(is_text(fact) for fact in facts):
            raise ValueError("元記事の本文で確認した事実を指定してください")
        selected[category] = {**article, "facts": facts}
        if is_text(reason):
            selected[category]["reason"] = reason
    for category in SLOTS:
        if category not in selected and category not in omitted:
            if any(article["category"] == category for article in candidates.values()):
                raise ValueError(f"採用記事または本文確認後の省略理由を指定してください: {category}")
            omitted[category] = "期間内に一次選別を通過したRSS候補なし"
    articles = []
    for category in SLOTS:
        if category in selected:
            article = selected[category]
            request(article["url"])
            articles.append(article)
    return articles, omitted


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_episode(ep, *, reviewed=True):
    if ep["format"] != "monoradio/news-1" or ep["lang"] != "ja":
        raise ValueError("台本形式または言語が違います")
    if date.fromisoformat(ep["date"]).isoformat() != ep["date"] or not is_text(ep["title"]):
        raise ValueError("日付または番組タイトルが不正です")
    if not 1 <= len(ep["news"]) <= len(SLOTS):
        raise ValueError(f"ニュースは1〜{len(SLOTS)}本必要です")
    for block in [ep["opening"], *(n["lines"] for n in ep["news"]), ep["closing"]]:
        if not isinstance(block, list) or not block:
            raise ValueError("読み上げ文がありません")
        for line in block:
            if set(line) != {"say", "show"} or not all(is_text(value) for value in line.values()):
                raise ValueError("各文に say と show が必要です")
            if reviewed and any(re.search(r"[。！？].+", text.strip(), re.S) or "\n" in text.strip() for text in line.values()):
                raise ValueError(f"say は1文ずつ指定してください: {line['say']}")
    for news in ep["news"]:
        link = news["link"]
        if (not is_text(news["label"]) or not isinstance(news["points"], list)
                or not news["points"] or not all(is_text(point) for point in news["points"])
                or news["url"] != link["url"] or not https_url(news["url"])
                or set(link) != {"url", "site", "title", "description"}):
            raise ValueError("ニュースの要点または出典が不正です")


def draft(sources, output):
    now = datetime.now(JST)
    articles, _ = select_articles(read_json(sources), candidate_articles(now))
    count = len(articles)
    if not count:
        raise ValueError("台本に使える記事がありません")
    station = read_json(ROOT / "station.json")
    line = {"type": "object", "properties": {"say": {"type": "string"}, "show": {"type": "string"}},
            "required": ["say", "show"], "additionalProperties": False}
    lines = {"type": "array", "items": line, "minItems": 1}
    news = {"type": "object", "properties": {
        "label": {"type": "string"}, "points": {"type": "array", "items": {"type": "string"}},
        "lines": lines,
    }, "required": ["label", "points", "lines"], "additionalProperties": False}
    schema = {"type": "object", "properties": {
        "title": {"type": "string"}, "opening": lines, "closing": lines,
        "news": {"type": "array", "items": news, "minItems": count, "maxItems": count},
    }, "required": ["title", "opening", "closing", "news"], "additionalProperties": False}
    instructions = (
        f"{now.date()}の日本語ニュース番組「{station['name']}」の短い台本を書いてください。"
        f"ニュースは入力順に{count}本。ニュースごとに見出し、短い要点2つと本文2〜3文。"
        "オープニングと締めは各1〜2文。1つのsay/showには1文だけを入れてください。"
        "showは自然な字幕表記、sayは同じ意味を保ち、数字や英字と難読語を読みの仮名にしてください。"
        "記事ごとのfactsだけを根拠に、短く言い換えてください。推測、転載、個人情報、連絡先を含めないでください。"
        "記事中の配信日などを省略せず、番組の次回配信日は約束しないでください。JSONだけを返してください。"
    )
    reply = json.loads(request("http://localhost:11434/api/generate", {
        "model": "gemma4:12b", "stream": False, "format": schema, "think": False,
        "options": {"num_ctx": 8192, "temperature": 0.2, "num_predict": 2400},
        "system": instructions, "prompt": json.dumps([
            {key: article[key] for key in ("title", "category", "facts")} for article in articles
        ], ensure_ascii=False),
    }, timeout=360))
    if reply.get("done_reason") != "stop":
        raise ValueError("Ollamaが台本を最後まで生成できませんでした")
    episode = json.loads(reply["response"])
    episode.update(format="monoradio/news-1", date=now.date().isoformat(), lang="ja")
    for item, article in zip(episode["news"], articles, strict=True):
        item["url"] = article["url"]
        item["link"] = {
            "url": article["url"], "title": item["label"],
            "site": article["site"], "description": "。".join(item["points"]),
        }
    validate_episode(episode, reviewed=False)
    with output.open("x", encoding="utf-8") as file:
        json.dump(episode, file, ensure_ascii=False, indent=2)
        file.write("\n")


def voice_id():
    return next(style["id"] for speaker in json.loads(request(f"{VOICE}/speakers"))
                if speaker["name"] == "春日部つむぎ"
                for style in speaker["styles"] if style["name"] == "ノーマル")


def synthesize(ep, wav, speaker):
    parts = [("opening", None, ep["opening"])]
    parts.extend(("news", i, item["lines"]) for i, item in enumerate(ep["news"]))
    parts.append(("closing", None, ep["closing"]))
    rate, cursor = 24000, 0
    timed, segments = [], []
    with wave.open(str(wav), "wb") as combined:
        combined.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        for part, (kind, index, lines) in enumerate(parts):
            start = round(cursor / rate, 3)
            for i, line in enumerate(lines):
                query = json.loads(request(
                    f"{VOICE}/audio_query?" + urlencode({"speaker": speaker, "text": line["say"]}), {}))
                query.update(outputSamplingRate=rate, outputStereo=False,
                             prePhonemeLength=0.08, postPhonemeLength=0.08)
                result = request(f"{VOICE}/synthesis?speaker={speaker}", query, timeout=90)
                with wave.open(io.BytesIO(result)) as sentence:
                    if (sentence.getnchannels(), sentence.getsampwidth(), sentence.getframerate()) != (1, 2, rate):
                        raise ValueError("VOICEVOXの音声形式が想定と異なります")
                    frames = sentence.getnframes()
                    combined.writeframesraw(sentence.readframes(frames))
                timed.append({"start": round(cursor / rate, 3), "end": round((cursor + frames) / rate, 3),
                              "show": line["show"], "part": part})
                pause = 0.22 if i < len(lines) - 1 else 0.58
                silence = round(pause * rate)
                combined.writeframesraw(bytes(silence * 2))
                cursor += frames + silence
            segments.append({"kind": kind, "index": index, "start": start, "end": round(cursor / rate, 3)})
    return timed, segments


def render(ep):
    validate_episode(ep)
    episode_dir = ROOT / "episodes"
    audio_dir = ROOT / "audio"
    index_path = ROOT / "episodes.json"
    stem = f"{ep['date']}-ja"
    episode_path, audio_path = episode_dir / f"{stem}.json", audio_dir / f"{stem}.mp3"
    episode_dir.mkdir(exist_ok=True)
    audio_dir.mkdir(exist_ok=True)
    # Keep the index unchanged unless both new files have been written.
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        temp = Path(tmp)
        index = read_json(index_path)
        if any(item["date"] == ep["date"] for item in index):
            raise ValueError("この日付はすでにエピソード一覧にあります")
        if episode_path.exists() or audio_path.exists():
            raise FileExistsError(f"{stem} の台本または音声が既にあります")
        wav, mp3 = temp / "lines.wav", temp / audio_path.name
        timed, segments = synthesize(ep, wav, voice_id())
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(wav), "-af",
                        "loudnorm=I=-16:TP=-1.5:LRA=7", "-ar", "24000", "-c:a", "libmp3lame",
                        "-b:a", "96k", "-map_metadata", "-1", str(mp3)], check=True)
        seconds = round(float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(mp3)], text=True)), 3)
        if abs(seconds - segments[-1]["end"]) > 0.15:
            raise ValueError("連結音声とMP3の再生時間が一致しません")
        segments[-1]["end"] = seconds
        ep.update(lines_timed=timed, segments=segments, seconds=seconds, made_at=datetime.now(JST).isoformat())
        index.append({"date": ep["date"], "ja": {"title": ep["title"], "seconds": seconds}})
        index.sort(key=lambda item: item["date"], reverse=True)
        write_json(temp / episode_path.name, ep)
        write_json(temp / index_path.name, index)
        installed = []
        try:
            for target in (audio_path, episode_path):
                # Hard-link creation fails if another invocation has already created the target.
                target.hardlink_to(temp / target.name)
                installed.append(target)
            (temp / index_path.name).replace(index_path)
        except OSError:
            for target in installed:
                target.unlink()
            raise
    print(f"{stem}: {seconds:.3f}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    candidates_command = commands.add_parser("candidates")
    candidates_command.add_argument("--include-excluded", action="store_true")
    select_command = commands.add_parser("select", help="確認済み記事の選定のみ")
    select_command.add_argument("sources", type=Path)
    draft_command = commands.add_parser("draft")
    draft_command.add_argument("sources", type=Path)
    draft_command.add_argument("output", type=Path)
    render_command = commands.add_parser("render")
    render_command.add_argument("script", type=Path)
    args = parser.parse_args()
    if args.command == "candidates":
        articles = candidate_articles(datetime.now(JST), include_excluded=args.include_excluded)
        print(json.dumps(list(articles.values()), ensure_ascii=False, indent=2))
    elif args.command == "select":
        selected, omitted = select_articles(read_json(args.sources), candidate_articles(datetime.now(JST)))
        report = [{key: value for key, value in article.items() if key != "facts"} for article in selected]
        print(json.dumps({"selected": report, "omitted": omitted}, ensure_ascii=False, indent=2))
    elif args.command == "draft":
        draft(args.sources, args.output)
    else:
        render(read_json(args.script))


if __name__ == "__main__":
    main()
