#!/usr/bin/env python3
"""Catalog every Animash fusion from The Unofficial Animash Wiki.

Usage:
    python scraper/scrape_wiki.py                 # full run -> data/fusions.json
    python scraper/scrape_wiki.py --only Dragon   # one animal (debugging)
    python scraper/scrape_wiki.py --refresh       # ignore the page cache

The wiki runs MediaWiki, so this uses its API instead of scraping pages.
Two sources are combined, because neither is complete on its own:

  A. Fusion pages. Every fusion has a page filed under Category:Fusions and
     categorised by both parents and its star tier (e.g. Smushko is in
     Gecko, Pug and Rare). The API returns categories for hundreds of pages
     per call, so this covers the whole wiki quickly. Primary source.
  B. Animal pages. Each has a "<Animal> Combinations" table, but editors
     only filled these in for popular animals (Dragon, Unicorn...). Still
     parsed, merged in, and disagreements kept in "conflicts".

Plus each animal's lead picture is downloaded to data/icons/ — the app
matches shelf screenshots against these.

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


TIER_STARS = {
    "common": 3, "unique": 4, "rare": 5, "ultra rare": 6, "legendary": 7, "mythical": 8,
    "divine": 9, "supreme": 10, "alpha supreme": 11, "gamma supreme": 12, "zeta supreme": 13,
    "theta supreme": 14, "kappa supreme": 15,
}
STAR_WORD_RE = re.compile(r"(\d+)\s*(?:stars?|★|\*)", re.I)


def parse_stars(text: str) -> tuple[int | None, str | None]:
    """'Legendary (7)' / '7 Stars' / '★★★★' / 'Legendary' -> (7, 'Legendary')."""
    t = text.strip()
    m = STAR_RE.search(t)
    if m:
        return int(m.group(1)), (t[: m.start()].strip(" -:") or None)
    m = STAR_WORD_RE.search(t)
    if m:
        return int(m.group(1)), None
    if "★" in t or "⭐" in t:
        return t.count("★") + t.count("⭐"), None
    key = re.sub(r"\s+", " ", t.lower())
    if key in TIER_STARS:
        return TIER_STARS[key], t
    return None, None


def _header_cells(table):
    """First row that looks like a header: <th> cells, or bold <td> cells."""
    for tr in table.find_all("tr"):
        ths = tr.find_all("th")
        if ths:
            return [th.get_text(" ", strip=True).lower() for th in ths]
        tds = tr.find_all("td")
        if tds and all(td.find(["b", "strong"]) for td in tds):
            return [td.get_text(" ", strip=True).lower() for td in tds]
    return []


def _col(headers: list[str], *keys: str) -> int | None:
    for k in keys:
        for i, h in enumerate(headers):
            if k in h:
                return i
    return None


def parse_fusion_table(html: str, animal: str) -> list[dict]:
    """Return rows [{a, b, name, stars, tier, icon}] from an animal page.

    Pages can hold several combination tables (one per animal set), and the
    column labels vary between editors, so every table on the page is tried
    and rows are de-duplicated by parent."""
    soup = BeautifulSoup(html, "html.parser")
    rows: dict[str, dict] = {}
    for table in soup.find_all("table"):
        headers = _header_cells(table)
        pc = _col(headers, "parent", "animal", "partner", "combine", "with", "mother", "father")
        sc = _col(headers, "star", "rank", "rarity", "tier")
        nc = _col(headers, "name", "fusion", "result", "animash", "child", "offspring")
        ic = _col(headers, "icon", "image", "picture")
        if pc is None or sc is None:
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= max(pc, sc):
                continue
            parent = tds[pc].get_text(" ", strip=True)
            if not parent or parent.lower() == animal.lower():
                continue
            stars, tier = parse_stars(tds[sc].get_text(" ", strip=True))
            if stars is None:
                continue
            name = tds[nc].get_text(" ", strip=True) if nc is not None and nc < len(tds) else None
            rows.setdefault(parent, {
                "a": animal, "b": parent, "name": name or None, "stars": stars, "tier": tier,
                "icon": _img_src(tds[ic]) if ic is not None and ic < len(tds) else None,
            })
    return list(rows.values())


def dump_tables(html: str) -> None:
    """Debug aid: print every table's header row and first data row."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"  {len(tables)} table(s) on page")
    for i, table in enumerate(tables):
        print(f"  table {i}: headers={_header_cells(table)}")
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if tds:
                print("    first row:", [td.get_text(' ', strip=True)[:30] for td in tds])
                break


# ------------------------------------------------------------ fusion pages
def _category_pages(category: str, pages: dict[int, dict]) -> int:
    """Add every page in `category` (with its categories + lead image) to `pages`."""
    cont: dict = {}
    seen = 0
    while True:
        data = api({
            "action": "query", "generator": "categorymembers",
            "gcmtitle": category, "gcmnamespace": 0, "gcmlimit": 500,
            "prop": "categories|pageimages", "cllimit": "max", "piprop": "original",
            **cont,
        })
        for pg in data.get("query", {}).get("pages", []):
            cur = pages.setdefault(pg["pageid"], {"title": pg["title"], "categories": set(), "image": None})
            cur["categories"] |= {c["title"].split(":", 1)[1] for c in pg.get("categories", [])}
            if pg.get("original"):
                cur["image"] = pg["original"]["source"]
            seen += 1
        cont = data.get("continue")
        if not cont:
            break
        time.sleep(0.3)
    return seen


def tier_categories() -> list[str]:
    """Sub-categories of Category:Star Ranks, e.g. 'Category:Common' ... 'Category:Kappa Supreme'.
    Falls back to the known tier names if the parent category can't be listed."""
    try:
        data = api({"action": "query", "list": "categorymembers", "cmtitle": "Category:Star Ranks",
                    "cmnamespace": 14, "cmlimit": 500})
        cats = [m["title"] for m in data["query"]["categorymembers"]]
    except Exception as e:
        print(f"  could not list Category:Star Ranks ({e}); using built-in tier names", file=sys.stderr)
        cats = []
    return cats or ["Category:" + t.title() for t in TIER_STARS]


def fusion_pages() -> list[dict]:
    """Every fusion page, found through the star-tier categories (every fusion
    has exactly one) plus Category:Fusions, with categories and lead image."""
    pages: dict[int, dict] = {}
    for cat in tier_categories() + ["Category:Fusions"]:
        n = _category_pages(cat, pages)
        print(f"  {cat}: {n} pages")
    return list(pages.values())


def fusion_row_from_categories(page: dict, canon: dict[str, str]) -> dict | None:
    """Turn a fusion page's categories into a {a, b, name, stars, tier, icon} row.
    Returns None (caller logs it) unless exactly two parent animals and a tier are found."""
    parents, tier, stars = [], None, None
    for c in page["categories"]:
        key = c.lower()
        if key in TIER_STARS:
            tier, stars = c, TIER_STARS[key]
            continue
        m = STAR_RE.search(c)
        if m and stars is None:
            tier, stars = c[: m.start()].strip() or None, int(m.group(1))
            continue
        a = canon.get(key) or canon.get(key.replace("-", " ")) or canon.get(key.replace(" ", ""))
        if a and a not in parents:
            parents.append(a)
    if len(parents) != 2 or stars is None:
        return None
    parents.sort(key=str.lower)
    return {"a": parents[0], "b": parents[1], "name": page["title"], "stars": stars,
            "tier": tier, "icon": page["image"], "source": "fusion page"}


# -------------------------------------------------------------------- icons
ICON_DIR = ROOT / "data" / "icons"


def _safe(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", title)


def page_image_urls(titles: list[str]) -> dict[str, str]:
    """title -> URL of the page's lead image (MediaWiki PageImages), 50 titles per call."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        data = api({"action": "query", "prop": "pageimages", "piprop": "original",
                    "titles": "|".join(chunk)})
        for pg in data["query"].get("pages", []):
            url = (pg.get("original") or {}).get("source")
            if url:
                out[pg["title"]] = url
        time.sleep(0.3)
    return out


def first_content_image(html: str) -> str | None:
    """Fallback: first real image in the page body (before the fusion table)."""
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if src.startswith("http") and "/images/" in src and "revision" in src:
            return src.split("/revision/")[0] + "/revision/latest"
    return None


def download_icons(animals: list[str], refresh: bool = False) -> dict[str, str]:
    """Save each animal's reference picture as data/icons/<Animal>.png. Returns title -> file."""
    from io import BytesIO

    from PIL import Image

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    print("Looking up page images ...")
    urls = page_image_urls(animals)
    saved: dict[str, str] = {}
    for i, title in enumerate(animals, 1):
        dest = ICON_DIR / (_safe(title) + ".png")
        if dest.exists() and not refresh:
            saved[title] = dest.name
            continue
        url = urls.get(title)
        if not url:
            try:
                url = first_content_image(page_html(title))
            except Exception:
                url = None
        if not url:
            print(f"  [{i}/{len(animals)}] {title}: no image found", file=sys.stderr)
            continue
        # strip thumbnail sizing so we get the full upload
        url = url.split("/revision/")[0] + "/revision/latest" if "/revision/" in url else url
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            Image.open(BytesIO(r.content)).convert("RGBA").save(dest)
            saved[title] = dest.name
            print(f"  [{i}/{len(animals)}] {title}: {dest.name}")
        except Exception as e:
            print(f"  [{i}/{len(animals)}] {title}: download failed ({e})", file=sys.stderr)
        time.sleep(0.3)
    return saved


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
        source = r.get("source") or r["a"]
        cur = fusions.get(key)
        if cur is None:
            fusions[key] = {
                "a": key[0], "b": key[1], "name": r["name"], "stars": r["stars"],
                "tier": r["tier"], "icon": r["icon"], "seen_on": [source],
            }
            continue
        if source not in cur["seen_on"]:
            cur["seen_on"].append(source)
        if not cur["icon"] and r["icon"]:
            cur["icon"] = r["icon"]
        if not cur["name"] and r["name"]:
            cur["name"] = r["name"]
        same_name = (cur["name"] or "").lower() == (r["name"] or "").lower() or not r["name"] or not cur["name"]
        if cur["stars"] != r["stars"] or not same_name:
            conflicts.append({
                "pair": list(key),
                "kept": {"name": cur["name"], "stars": cur["stars"], "from": cur["seen_on"][0]},
                "other": {"name": r["name"], "stars": r["stars"], "from": source},
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
    ap.add_argument("--no-icons", action="store_true", help="skip downloading reference pictures")
    ap.add_argument("--icons-only", action="store_true", help="only (re)download reference pictures")
    ap.add_argument("--dump", nargs="+", metavar="TITLE", help="print the tables found on these pages and exit")
    args = ap.parse_args()

    if args.dump:
        for title in args.dump:
            print(title)
            dump_tables(page_html(title, args.refresh))
        return 0

    print("Listing Category:Animals ...")
    animals = list_animals()
    print(f"  {len(animals)} animal pages")
    targets = args.only or animals

    icons: dict[str, str] = {}
    if not args.no_icons:
        icons = download_icons(targets, args.refresh)
        print(f"  {len(icons)}/{len(targets)} reference pictures in {ICON_DIR}")
        if args.icons_only:
            return 0

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

    fusion_page_rows, odd_fusion_pages = [], []
    if not args.only:
        print("Reading fusion pages (Category:Fusions) ...")
        canon = _canon_map(animals)
        pages = fusion_pages()
        for pg in pages:
            row = fusion_row_from_categories(pg, canon)
            if row:
                fusion_page_rows.append(row)
            else:
                odd_fusion_pages.append(pg["title"])
        print(f"  {len(pages)} fusion pages -> {len(fusion_page_rows)} usable rows "
              f"({len(odd_fusion_pages)} without two parents + a tier)")

    merged = merge(fusion_page_rows + all_rows, animals)
    out = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": WIKI,
        "animal_count": len(animals),
        "fusion_count": len(merged["fusions"]),
        "animals": animals,
        "icons": icons,
        "fusions": merged["fusions"],
        "conflicts": merged["conflicts"],
        "unmatched_parents": merged["unmatched_parents"],
        "pages_without_table": missing,
        "fusion_pages_unusable": odd_fusion_pages,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\nWrote {args.out}")
    print(f"  fusions: {out['fusion_count']}   conflicts: {len(out['conflicts'])}   "
          f"unmatched parents: {len(out['unmatched_parents'])}   animal pages w/o table: {len(missing)}   "
          f"fusion pages unusable: {len(odd_fusion_pages)}")
    if out["unmatched_parents"]:
        print("  unmatched parent names (check spelling on wiki):", ", ".join(out["unmatched_parents"][:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
