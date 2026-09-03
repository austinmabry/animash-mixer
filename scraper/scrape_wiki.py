#!/usr/bin/env python3
"""Catalog every Animash fusion from The Unofficial Animash Wiki.

Usage:
    python scraper/scrape_wiki.py                 # full run -> data/fusions.json
    python scraper/scrape_wiki.py --only Dragon   # one animal (debugging)
    python scraper/scrape_wiki.py --refresh       # ignore the page cache

The wiki runs MediaWiki, so this uses its API instead of scraping pages:
  1. list Category:Animals (namespace 0 only -> real animal pages, not
     the per-animal sub-categories)
  2. for each animal, action=parse -> rendered HTML of the page
  3. find the fusion table (headers Parent / Name / Icon / Star Rank)

Fusions are symmetric (Dragon+Alien == Alien+Dragon). Both directions are
merged into one record; disagreements are kept in "conflicts" rather than
silently resolved so you can eyeball them.

Every page is cached under data/cache/ so re-runs after a network hiccup
only fetch what is missing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

WIKI = "https://the-unofficial-animash.fandom.com"
API = f"{WIKI}/api.php"
# Fandom answers 402/403 to anonymous-looking clients; a normal browser UA works.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 AnimashMixer/1.0")

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "fusions.json"

STAR_RE = re.compile(r"\((\d+)\)")

session = requests.Session()
session.headers["User-Agent"] = UA


# ----------------------------------------------------------------- wiki API
def api(params: dict, retries: int = 4) -> dict:
    params = {"format": "json", "formatversion": 2, **params}
    last = None
    for attempt in range(retries):
        r = session.get(API, params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"API error: {data['error']}")
            return data
        last = r
        wait = 2 ** attempt
        print(f"  HTTP {r.status_code} from wiki, retrying in {wait}s", file=sys.stderr)
        time.sleep(wait)
    last.raise_for_status()
    raise RuntimeError("unreachable")


def list_animals() -> list[str]:
    names, cont = [], {}
    while True:
        data = api({
            "action": "query", "list": "categorymembers",
            "cmtitle": "Category:Animals", "cmnamespace": 0,
            "cmlimit": 500, **cont,
        })
        names += [m["title"] for m in data["query"]["categorymembers"]]
        cont = data.get("continue")
        if not cont:
            break
    return sorted(set(names), key=str.lower)


def page_html(title: str, refresh: bool = False) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (re.sub(r"[^A-Za-z0-9_-]", "_", title) + ".html")
    if f.exists() and not refresh:
        return f.read_text(encoding="utf-8")
    data = api({"action": "parse", "page": title, "prop": "text"})
    html = data["parse"]["text"]
    f.write_text(html, encoding="utf-8")
    time.sleep(0.4)  # be polite; ~360 pages -> a couple of minutes
    return html


# ------------------------------------------------------------------ parsing
def _img_src(cell) -> str | None:
    img = cell.find("img") if cell else None
    if not img:
        return None
    for attr in ("data-src", "src"):
        src = img.get(attr)
        if src and not src.startswith("data:"):
            return src.split("/revision/")[0] + "/revision/latest" if "/revision/" in src else src
    return None


def parse_fusion_table(html: str, animal: str) -> list[dict]:
    """Return rows [{a, b, name, stars, tier, icon}] from an animal page."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for table in soup.find_all("table"):
        header_row = next((tr for tr in table.find_all("tr") if tr.find("th")), None)
        if not header_row:
            continue
        headers = [th.get_text(" ", strip=True).lower() for th in header_row.find_all("th")]
        if not any("parent" in h for h in headers) or not any("star" in h for h in headers):
            continue

        def col(key: str) -> int | None:
            return next((i for i, h in enumerate(headers) if key in h), None)

        pc, nc, ic, sc = col("parent"), col("name"), col("icon"), col("star")
        if pc is None or nc is None or sc is None:
            continue

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= max(pc, nc, sc):
                continue
            parent = tds[pc].get_text(" ", strip=True)
            name = tds[nc].get_text(" ", strip=True)
            star_text = tds[sc].get_text(" ", strip=True)
            m = STAR_RE.search(star_text)
            if not parent or not m:
                continue
            tier = star_text[: m.start()].strip() or None
            rows.append({
                "a": animal,
                "b": parent,
                "name": name or None,
                "stars": int(m.group(1)),
                "tier": tier,
                "icon": _img_src(tds[ic]) if ic is not None and ic < len(tds) else None,
            })
        if rows:
            break  # first matching table is the combinations table
    return rows


# ------------------------------------------------------------------ merging
def _canon_map(animals: list[str]) -> dict[str, str]:
    """lowercase / de-hyphenated / de-spaced -> canonical page title"""
    m = {}
    for a in animals:
        for key in {a.lower(), a.lower().replace("-", " "), a.lower().replace(" ", "")}:
            m.setdefault(key, a)
    return m


def merge(all_rows: list[dict], animals: list[str]) -> dict:
    canon = _canon_map(animals)
    fusions: dict[tuple, dict] = {}
    conflicts, unmatched = [], set()

    for r in all_rows:
        b_key = r["b"].lower()
        b = canon.get(b_key) or canon.get(b_key.replace("-", " ")) or canon.get(b_key.replace(" ", ""))
        if not b:
            unmatched.add(r["b"])
            b = r["b"]
        key = tuple(sorted((r["a"], b), key=str.lower))
        cur = fusions.get(key)
        if cur is None:
            fusions[key] = {
                "a": key[0], "b": key[1], "name": r["name"], "stars": r["stars"],
                "tier": r["tier"], "icon": r["icon"], "seen_on": [r["a"]],
            }
            continue
        if r["a"] not in cur["seen_on"]:
            cur["seen_on"].append(r["a"])
        if not cur["icon"] and r["icon"]:
            cur["icon"] = r["icon"]
        if not cur["name"] and r["name"]:
            cur["name"] = r["name"]
        same_name = (cur["name"] or "").lower() == (r["name"] or "").lower() or not r["name"] or not cur["name"]
        if cur["stars"] != r["stars"] or not same_name:
            conflicts.append({
                "pair": list(key),
                "kept": {"name": cur["name"], "stars": cur["stars"], "from": cur["seen_on"][0]},
                "other": {"name": r["name"], "stars": r["stars"], "from": r["a"]},
            })

    return {
        "fusions": sorted(fusions.values(), key=lambda f: (-f["stars"], f["a"].lower(), f["b"].lower())),
        "conflicts": conflicts,
        "unmatched_parents": sorted(unmatched, key=str.lower),
    }


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="only these animal page titles")
    ap.add_argument("--refresh", action="store_true", help="ignore data/cache")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    print("Listing Category:Animals ...")
    animals = list_animals()
    print(f"  {len(animals)} animal pages")
    targets = args.only or animals

    all_rows, missing = [], []
    for i, title in enumerate(targets, 1):
        try:
            rows = parse_fusion_table(page_html(title, args.refresh), title)
        except Exception as e:  # keep going; the page is skipped and reported
            print(f"  [{i}/{len(targets)}] {title}: ERROR {e}", file=sys.stderr)
            missing.append(title)
            continue
        if not rows:
            missing.append(title)
        print(f"  [{i}/{len(targets)}] {title}: {len(rows)} rows")
        all_rows += rows

    merged = merge(all_rows, animals)
    out = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": WIKI,
        "animal_count": len(animals),
        "fusion_count": len(merged["fusions"]),
        "animals": animals,
        "fusions": merged["fusions"],
        "conflicts": merged["conflicts"],
        "unmatched_parents": merged["unmatched_parents"],
        "pages_without_table": missing,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\nWrote {args.out}")
    print(f"  fusions: {out['fusion_count']}   conflicts: {len(out['conflicts'])}   "
          f"unmatched parents: {len(out['unmatched_parents'])}   pages w/o table: {len(missing)}")
    if out["unmatched_parents"]:
        print("  unmatched parent names (check spelling on wiki):", ", ".join(out["unmatched_parents"][:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
