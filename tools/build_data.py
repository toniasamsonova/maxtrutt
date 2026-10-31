#!/usr/bin/env python3
"""Art News Wire data builder.

The Google Sheet "Art News Wire" (tab "Wire") is the store. Its columns, in order:
published | precision | publication | section | type | topic | access | headline | headline_en |
subtitle | summary | author | url | image | clipping | fetched | pick

Usage:
  python3 build_data.py from-rows rows.json data.json     rows.json = get_values result for 'Wire'!A:Q (header row first)
  python3 build_data.py from-records records.py data.json  records.py defines RECORDS (initial build only)
  python3 build_data.py rows-from-records records.py rows.json   sheet rows (no header) from RECORDS

data.json is what the website reads. Articles are sorted newest first (date-only items at the end of their day).
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

COLUMNS = ["published", "precision", "publication", "section", "type", "topic", "access", "headline",
           "headline_en", "subtitle", "summary", "author", "url", "image", "clipping", "fetched", "pick"]
TYPES = {"News", "Commentary", "Review", "Feature", "Interview"}
TOPICS = {"Exhibitions", "Museums & institutions", "Art market", "Artists", "Policy & heritage"}
ACCESS = {"Free to read", "Subscription", "Metered", "Registration required", "Access unconfirmed"}


def clean(s):
    if s is None:
        return ""
    s = str(s).strip()
    return s.replace("—", ",").replace(" ,", ",")


def sort_key(a):
    p = a["published"]
    if not p:
        return (0, "")
    if len(p) == 10:  # date only: sorts after every timed item of that day (newest first order)
        return (1, p + "T00:00:00+00:00")
    try:
        dt = datetime.fromisoformat(p)
        return (1, dt.astimezone(timezone.utc).isoformat())
    except ValueError:
        return (0, "")


def normalise(rec):
    a = {k: clean(rec.get(k, "")) for k in COLUMNS if k != "pick"}
    a["published"] = a["published"]
    if a["published"] and len(a["published"]) == 10:
        a["precision"] = "date"
    elif a["published"] and not a["precision"]:
        a["precision"] = "approx"
    if a["type"] not in TYPES:
        a["type"] = "News"
    if a["topic"] not in TOPICS:
        a["topic"] = ""
    if a["access"] not in ACCESS:
        a["access"] = "Access unconfirmed"
    a["id"] = hashlib.sha1(a["url"].encode("utf-8")).hexdigest()[:12]
    return a


def build(records):
    seen = set()
    arts = []
    for r in records:
        a = normalise(r)
        if not a["url"] or not a["headline"]:
            continue
        key = a["url"].rstrip("/").split("?")[0].lower()
        if key in seen:
            continue
        seen.add(key)
        arts.append(a)
    arts.sort(key=sort_key, reverse=True)
    pubs = sorted({a["publication"] for a in arts}, key=str.lower)
    return {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "timezone": "Europe/London",
        "window_start": "2026-10-30T00:00:00+00:00",
        "count": len(arts),
        "publications": pubs,
        "articles": arts,
    }


def rows_to_records(rows):
    header = [h.strip().lower() for h in rows[0]]
    out = []
    for row in rows[1:]:
        if not row or not any(str(c).strip() for c in row):
            continue
        rec = {}
        for i, h in enumerate(header):
            if i < len(row):
                rec[h] = row[i]
        out.append(rec)
    return out


def load_records_py(path):
    ns = {}
    exec(open(path, encoding="utf-8").read(), ns)
    return ns["RECORDS"]


def main():
    mode, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
    if mode == "from-rows":
        rows = json.load(open(src, encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("values", [])
        data = build(rows_to_records(rows))
    elif mode == "from-records":
        data = build(load_records_py(src))
    elif mode == "rows-from-records":
        fetched = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        arts = build(load_records_py(src))["articles"]
        rows = []
        for a in arts:
            a["fetched"] = fetched
            cells = [a.get(c, "") for c in COLUMNS[1:-1]]
            # Sheets treats a leading apostrophe as a text marker, so double it to keep a real one.
            cells = [("'" + c) if isinstance(c, str) and c.startswith("'") else c for c in cells]
            rows.append(["'" + a["published"]] + cells + [""])
        json.dump(rows, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
        print(len(rows), "rows")
        return
    else:
        raise SystemExit("unknown mode")
    json.dump(data, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(data["count"], "articles ->", dst)


if __name__ == "__main__":
    main()
