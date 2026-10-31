#!/usr/bin/env python3
"""Reads each source's public RSS/Atom feed, keeps art stories from the window start onward,
merges them into data.json (existing curated items are kept) and records which feeds worked.
Run by GitHub Actions every 5 hours. Standard library plus feedparser."""
import hashlib, html, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import feedparser
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from build_data import sort_key

UA = "Mozilla/5.0 (compatible; MTPostNewsWire/1.0; +https://github.com/toniasamsonova/maxtrutt)"
DATA = "data.json"
ART = re.compile(r"\b(art|arts|artist|artists|artwork|artworks|gallery|galleries|museum|museums|exhibition|exhibitions|"
                 r"painting|paintings|painter|sculpture|sculptor|auction|auctions|christie'?s|sotheby'?s|phillips|biennale|biennial|"
                 r"curator|curators|collector|collectors|kunst|museo|mostra|mostre|exposition|exposición|ausstellung|"
                 r"galerie|musée|artiste|arte|photography|frieze|tate|louvre|moma|guggenheim|picasso|banksy)\b", re.I)
NOISE = re.compile(r"\b(martial arts|arts and crafts|state of the art|artificial|article|smart|start)\b", re.I)

def fetch(url, timeout=25):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, text/xml, */*"})
    with urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")

def discover(site):
    try:
        body, _ = fetch(site)
    except Exception:
        return []
    txt = body.decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", txt, re.I):
        h = re.search(r"href=[\"']([^\"']+)", m.group(0))
        if h:
            out.append(urljoin(site, html.unescape(h.group(1))))
    return out[:4]

def strip(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()

def tag_topic(t):
    t = t.lower()
    rules = [("Art market", r"auction|sotheby|christie|phillips|market|sale|sold|dealer|collector|fair|art basel|price|record"),
             ("Policy & heritage", r"restitution|repatriat|heritage|looted|stolen|ban|law|tax|culture secretary|ministry|funding|cuts|protest"),
             ("Museums & institutions", r"museum|musée|museo|institution|director|trustee|foundation|opens|opening|gallery"),
             ("Exhibitions", r"exhibition|show|biennale|biennial|retrospective|review|mostra|ausstellung|exposition"),
             ("Artists", r"artist|painter|sculptor|dies|death|obituary|interview")]
    for name, rx in rules:
        if re.search(rx, t):
            return name
    return ""

def tag_type(t, url):
    s = (t + " " + url).lower()
    if re.search(r"/review|review:|\breview\b", s): return "Review"
    if re.search(r"/opinion|/comment|/editorial|opinion:", s): return "Commentary"
    if re.search(r"interview|in conversation|q&a", s): return "Interview"
    if re.search(r"/feature|long read", s): return "Feature"
    return "News"

def get_source(src, window):
    feeds = list(src["feeds"])
    log = {"name": src["name"], "ok": False, "items": 0, "feed": "", "error": ""}
    arts = []
    tried_disc = False
    while feeds or not tried_disc:
        if not feeds:
            tried_disc = True
            feeds = discover(src["site"])
            if not feeds:
                break
        url = feeds.pop(0)
        try:
            body, _ = fetch(url)
            f = feedparser.parse(body)
            if not f.entries:
                raise ValueError("no entries")
        except Exception as e:
            log["error"] = f"{type(e).__name__}: {e}"[:120]
            continue
        log.update(ok=True, feed=url, error="")
        for e in f.entries:
            link = e.get("link", "")
            title = strip(e.get("title", ""))
            if not link or not title:
                continue
            tm = e.get("published_parsed") or e.get("updated_parsed")
            if not tm:
                continue
            pub = datetime(*tm[:6], tzinfo=timezone.utc).isoformat()
            if pub < window:
                continue
            summ = strip(e.get("summary", ""))[:400]
            if src["art_only"]:
                cats = " ".join(t.get("term", "") for t in e.get("tags", []) or [])
                blob = NOISE.sub(" ", f"{title} {summ} {cats}")
                if not ART.search(blob):
                    continue
            author = strip(e.get("author", ""))
            arts.append(dict(published=pub, precision="exact", publication=src["name"], section="", type=tag_type(title, link),
                             topic=tag_topic(title + " " + summ), access="Access unconfirmed", headline=title, headline_en="",
                             subtitle="", summary=summ if summ.lower() != title.lower() else "", author=author, url=link,
                             image="", clipping="", fetched=datetime.now(timezone.utc).replace(microsecond=0).isoformat()))
        break
    log["items"] = len(arts)
    return arts, log

def get_lede(url):
    """Opening paragraph only (max 80 words), from the page's own markup."""
    try:
        body, _ = fetch(url, 20)
    except Exception:
        return ""
    t = body.decode("utf-8", "ignore")
    cands = []
    art = re.search(r"<article[\s\S]*?</article>", t, re.I)
    for m in re.finditer(r"<p[^>]*>([\s\S]*?)</p>", art.group(0) if art else t, re.I):
        cands.append(strip(m.group(1)))
    for m in re.finditer(r"<meta[^>]+(?:property=[\"']og:description[\"']|name=[\"']description[\"'])[^>]*content=[\"']([^\"']+)", t, re.I):
        cands.append(strip(m.group(1)))
    for c in cands:
        if len(c) >= 80 and not re.search(r"cookie|subscribe|sign in|javascript|privacy|consent", c, re.I):
            w = c.split()
            return " ".join(w[:80]) + (" …" if len(w) > 80 else "")
    return ""

def main():
    data = json.load(open(DATA, encoding="utf-8"))
    window = data["window_start"]
    sources = json.load(open("tools/feeds.json", encoding="utf-8"))
    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(lambda s: get_source(s, window), sources))
    reg = {s["name"]: s for s in data["sources"]}
    have = {a["url"].rstrip("/").split("?")[0].lower() for a in data["articles"]}
    added = 0
    for arts, _ in results:
        for a in arts:
            k = a["url"].rstrip("/").split("?")[0].lower()
            if k in have:
                continue
            have.add(k)
            a["id"] = hashlib.sha1(a["url"].encode()).hexdigest()[:12]
            s = reg.get(a["publication"], {})
            a.update(region=s.get("region", ""), kind=s.get("kind", ""), country=s.get("country", ""))
            data["articles"].append(a)
            added += 1
    todo = [a for a in data["articles"] if "lede" not in a][:60]
    with ThreadPoolExecutor(8) as ex:
        for a, l in zip(todo, ex.map(lambda x: get_lede(x["url"]), todo)):
            a["lede"] = l
    data["articles"] = [a for a in data["articles"] if a["published"] >= window[:10]]
    data["articles"].sort(key=sort_key, reverse=True)
    counts = {}
    for a in data["articles"]:
        counts[a["publication"]] = counts.get(a["publication"], 0) + 1
    ok = {l["name"]: l for _, l in results}
    for name, s in reg.items():
        s["items"] = counts.get(name, 0)
        if s["items"]:
            s["status"] = "live"
        elif name in ok and ok[name]["ok"]:
            s["status"] = "feed-ok"
        elif name in ok and s["status"] in ("queued", "live", "feed-ok"):
            s["status"] = "no-feed"
    data["publications"] = sorted(counts, key=str.lower)
    data["count"] = len(data["articles"])
    data["generated"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    json.dump(data, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump([l for _, l in results], open("tools/last_run.json", "w"), indent=1)
    good = sum(1 for _, l in results if l["ok"])
    print(f"feeds read: {good}/{len(results)}; new articles: {added}; total: {data['count']}; sources with items: {len(counts)}")

if __name__ == "__main__":
    main()
