# Animash Mixer

Phone web app for kids playing **Animash** (Roblox). Take a photo or screenshot
of the animals on your shelf; the app recognises them, checks every possible
pair against the catalog scraped from
[The Unofficial Animash Wiki](https://the-unofficial-animash.fandom.com), and
shows the five highest-star fusions you can make right now.

```
photo/screenshot ──► vision (Claude or local OCR) ──► animal names
                                                        │
wiki scrape ──► data/fusions.json ──► rank all pairs ◄──┘ ──► top 5 + stars
```

## 1. Build the catalog (once, then whenever the game adds animals)

```bash
pip install -r requirements.txt
python scraper/scrape_wiki.py
```

This walks `Category:Animals` through the wiki's MediaWiki API (~360 pages,
a few minutes), parses each page's *Combinations* table, merges the two
directions of every pair, and writes `data/fusions.json`. Pages are cached in
`data/cache/` so a re-run after a network blip only fetches what's missing
(`--refresh` forces a full re-fetch, `--only Dragon Alien` scrapes a subset).

The summary line at the end reports `conflicts` (the two animal pages disagree
on a star rank), `unmatched parents` (a table names an animal that has no
page, usually a spelling drift on the wiki) and `pages w/o table`. All three
are stored in the JSON so you can inspect them; nothing is silently fixed.

Until you run the scraper, the app boots on `data/fusions.sample.json`, which
is the Dragon page only (195 pairs) and is enough to smoke-test the UI.

## 2. Run it

```bash
cp .env.example .env      # add ANTHROPIC_API_KEY, or set VISION_BACKEND=ocr
docker compose up -d --build
```

Open `http://<host>:8080` on the kids' phones. Add to home screen and it
behaves like an app. Without Docker: `python -m app.server` (dev server on
`:8080`).

### Vision backends

| `VISION_BACKEND` | Needs | Recognises |
|---|---|---|
| `claude` (default) | `ANTHROPIC_API_KEY` | icons **and** text; handles photos of a screen |
| `ocr` | Tesseract (`docker build --build-arg WITH_OCR=true`) | only the name text under each icon; screenshots work far better than photos |

Either way the child can fix the list by hand: remove a chip, or type a name
(autocomplete over every animal in the catalog). Rankings recompute instantly.

## API

- `GET  /api/health` – catalog size, scrape date, whether sample data is loaded
- `GET  /api/animals` – all known animal names
- `POST /api/analyze` – multipart `image` → `{available, mixes, total_known, missing}`
- `POST /api/mixes` – JSON `{animals: [...], top_n: 5}` → same shape

`missing` lists pairs on the shelf that have no wiki entry yet, so "not in the
catalog" is never confused with "low stars".

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Layout

```
scraper/scrape_wiki.py   wiki → data/fusions.json
app/fusions.py           catalog + pair ranking
app/vision.py            image → animal names (claude | ocr)
app/server.py            Flask routes
static/                  phone UI (no build step)
data/fusions.sample.json Dragon-only sample so the app runs before scraping
tests/                   parser, merge, ranking, endpoint tests
```
