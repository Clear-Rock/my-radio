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
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import wave
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
FEEDS = (
    ("tech", "ITmedia AI+", "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml"),
    ("tech", "PC Watch", "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf"),
    ("games", "AUTOMATON", "https://automaton-media.com/feed/"),
    ("local", "宮崎市", "https://www.city.miyazaki.miyazaki.jp/city/public_relations/press_material/rss.xml"),
)
CATEGORIES = ("tech", "games", "local")
PC_EXCLUDED = re.compile(r"お買い得|セール|値引き|Sale|Sponsored|配信番組|生配信|生放送", re.I)
LOCAL_EXCLUDED = re.compile(r"逮捕|事件|事故|不正アクセス|流出|訃報")
VOICE = "http://localhost:50021"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request(url, payload=None, timeout=30):
    headers = {"User-Agent": "ClearRockRadio/1.0"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data, headers), timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}: {url}")
        return response.read()


def candidate_articles(now):
    articles = {}
    for category, site, url in FEEDS:
        for item in ET.fromstring(request(url)).iterfind(".//{*}item"):
            title = item.findtext("{*}title", "").strip()
            link = item.findtext("{*}link", "").strip()
            stamp = item.findtext("{*}pubDate") or item.findtext("{*}date")
            if not title or urlsplit(link).scheme != "https" or not stamp:
                continue
            try:
                published = datetime.fromisoformat(stamp) if re.match(r"^\d{4}-", stamp) else parsedate_to_datetime(stamp)
            except ValueError:
                continue
            if published.tzinfo is None or not now - timedelta(days=7) <= published <= now:
                continue
            if site == "PC Watch" and ("/todays_sales/" in link or PC_EXCLUDED.search(title)):
                continue
            if category == "local" and LOCAL_EXCLUDED.search(title):
                continue
            articles[link] = {
                "category": category, "site": site, "title": title, "url": link,
                "published": published.astimezone(JST).isoformat(),
            }
    return articles


def select_articles(review, candidates):
    if not isinstance(review, list) or len(review) != len(CATEGORIES):
        raise ValueError("元記事を確認した3件を指定してください")
    selected = []
    for category, entry in zip(CATEGORIES, review, strict=True):
        article = candidates.get(entry["url"])
        if not article or article["category"] != category:
            raise ValueError(f"指定カテゴリの直近7日間のRSS候補にありません: {entry['url']}")
        facts = entry["facts"]
        if not isinstance(facts, list) or not facts or not all(is_text(fact) for fact in facts):
            raise ValueError("元記事の本文で確認した事実を指定してください")
        # The editor verifies the body and eligibility before providing these notes.
        request(article["url"])
        selected.append({**article, "facts": facts})
    return selected


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_episode(ep, *, reviewed=True):
    if ep["format"] != "monoradio/news-1" or ep["lang"] != "ja":
        raise ValueError("台本形式または言語が違います")
    if date.fromisoformat(ep["date"]).isoformat() != ep["date"] or not is_text(ep["title"]):
        raise ValueError("日付または番組タイトルが不正です")
    if len(ep["news"]) != len(CATEGORIES):
        raise ValueError("ニュースは3本必要です")
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
                or news["url"] != link["url"] or urlsplit(news["url"]).scheme != "https"
                or set(link) != {"url", "site", "title", "description"}):
            raise ValueError("ニュースの要点または出典が不正です")


def draft(sources, output):
    now = datetime.now(JST)
    articles = select_articles(read_json(sources), candidate_articles(now))
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
        "news": {"type": "array", "items": news, "minItems": 3, "maxItems": 3},
    }, "required": ["title", "opening", "closing", "news"], "additionalProperties": False}
    instructions = (
        f"{now.date()}の日本語ニュース番組「{station['name']}」の短い台本を書いてください。"
        "ニュースは入力順に3本。ニュースごとに見出し、短い要点2つと本文2〜3文。"
        "オープニングと締めは各1〜2文。1つのsay/showには1文だけを入れてください。"
        "showは自然な字幕表記、sayは同じ意味を保ち、数字や英字と難読語を読みの仮名にしてください。"
        "記事ごとのfactsだけを根拠に、短く言い換えてください。推測、転載、個人情報、連絡先を含めないでください。"
        "記事中の配信日などを省略せず、番組の次回配信日は約束しないでください。JSONだけを返してください。"
    )
    reply = json.loads(request("http://localhost:11434/api/generate", {
        "model": "gemma4:12b", "stream": False, "format": schema, "think": False,
        "options": {"num_ctx": 8192, "temperature": 0.2, "num_predict": 2400},
        "system": instructions, "prompt": json.dumps(articles, ensure_ascii=False),
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
    commands.add_parser("candidates")
    draft_command = commands.add_parser("draft")
    draft_command.add_argument("sources", type=Path)
    draft_command.add_argument("output", type=Path)
    render_command = commands.add_parser("render")
    render_command.add_argument("script", type=Path)
    args = parser.parse_args()
    if args.command == "candidates":
        articles = candidate_articles(datetime.now(JST))
        print(json.dumps(list(articles.values()), ensure_ascii=False, indent=2))
    elif args.command == "draft":
        draft(args.sources, args.output)
    else:
        render(read_json(args.script))


if __name__ == "__main__":
    main()
