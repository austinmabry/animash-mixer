# Animash Mixer

Phone web app for kids playing **Animash** (Roblox). Take a photo or screenshot
of the animals on your shelf; the app recognises them, checks every possible
pair against the catalog scraped from
[The Unofficial Animash Wiki](https://the-unofficial-animash.fandom.com), and
shows the five highest-star fusions you can make right now.

```
screenshot ──► find white tiles ──► match each against data/icons/ ──► animal names
                                                                          │
wiki scrape ──► data/fusions.json + data/icons/ ──► rank every pair ◄─────┘ ──► top 5
```

The in-game shelf shows pictures only (names appear on hover), so
recognition is image matching, done locally with OpenCV: every white
circular tile is found, cropped, and compared against the reference picture
of every animal from the wiki. Colour and edge structure are compared
separately, so an orange pumpkin doesn't get confused with a green durian.
Nothing leaves your network and no API key is needed.

## 1. Build the catalog (once, then whenever the game adds animals)

```bash
pip install -r requirements.txt
python scraper/scrape_wiki.py
```

This walks `Category:Animals` through the wiki's MediaWiki API (~360 pages,
a few minutes), downloads each animal's lead picture to `data/icons/`, parses
each page's *Combinations* table, merges the two directions of every pair,
and writes `data/fusions.json`. `--icons-only` refreshes just the pictures;
`--no-icons` skips them. Pages are cached in
`data/cache/` so a re-run after a network blip only fetches what's missing
(`--refresh` forces a full re-fetch, `--only Dragon Alien` scrapes a subset).

The summary line at the end reports `conflicts` (the two animal pages disagree
on a star rank), `unmatched parents` (a table names an animal that has no
page, usually a spelling drift on the wiki) and `pages w/o table`. All three
are stored in the JSON so you can inspect them; nothing is silently fixed.

Until you run the scraper, the app boots on `data/fusions.sample.json`, which
is the Dragon page only (195 pairs) and is enough to smoke-test the UI.
Recognition needs `data/icons/` populated; the health endpoint and the footer
say how many pictures are loaded.

### Checking recognition against a real screenshot

```bash
python scripts/match_debug.py shelf.png
```

Prints every tile with its best guess, score and runner-up, and writes
`shelf.debug.png` with the tiles ringed green (confident), amber (close
call) or red (below `MATCH_THRESHOLD`, default 0.45). If one animal keeps
scoring low against the correct name, the wiki's picture doesn't match the
in-game art: crop that tile from a screenshot and save it as
`data/icons/<Animal>__2.png`. Every file for an animal competes and the
best one counts, so adding views only helps.

## 2. Run it

```bash
cp .env.example .env      # add ANTHROPIC_API_KEY, or set VISION_BACKEND=ocr
docker compose up -d --build
```

Open `http://<host>:35000` on the kids' phones. Add to home screen and it
behaves like an app. Without Docker: `python -m app.server` (dev server, honours PORT in .env, default 35000
`:35000`).

### Using it

Screenshot the "Pick a Dad / Mom" shelf. The shelf scrolls, so if the
animals don't all fit, scroll, screenshot again, and use **Add another
screenshot** — everything accumulates on one shelf. Tiles the matcher can't
call confidently show as dashed "?" chips; tap one to pick from its top
guesses. Anything can be removed, and any animal can be typed in with
autocomplete. Rankings recompute instantly.

### Vision backends

| `VISION_BACKEND` | Needs | Notes |
|---|---|---|
| `icons` (default) | `data/icons/` from the scraper | local, offline, tuned on a real shelf screenshot |
| `claude` | `ANTHROPIC_API_KEY` | asks Claude to name the animals; weak here because tiles carry no text |
| `ocr` | Tesseract (`--build-arg WITH_OCR=true`) | only useful for screens that show names |

## API

- `GET  /api/health` – catalog size, scrape date, whether sample data is loaded
- `GET  /api/animals` – all known animal names
- `POST /api/analyze` – multipart `image` → `{available, mixes, total_known, missing, tiles, unrecognised, not_in_catalog}`
  (`tiles` carries each tile's box, best guess, score and alternatives)
- `POST /api/mixes` – JSON `{animals: [...], top_n: 5}` → same shape

`missing` lists pairs on the shelf that have no wiki entry yet, so "not in the
catalog" is never confused with "low stars".

## Unraid

Same shape as any compose-managed container on the box:

```bash
ssh root@<unraid-ip>
mkdir -p /mnt/user/appdata/animash-mixer && cd /mnt/user/appdata/animash-mixer
git clone <your-repo-url> .
cp .env.example .env
docker compose up -d --build
docker exec -it animash-mixer python scraper/scrape_wiki.py   # once; data/ is a bind mount
```

Phones on the Wi-Fi open `http://<unraid-ip>:35000` and "Add to Home Screen".
For a hostname, add an Unbound host override (e.g. `animash.lan`) in OPNsense.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Layout

```
scraper/scrape_wiki.py   wiki → data/fusions.json
app/fusions.py           catalog + pair ranking
app/tiles.py             find the white tiles in a screenshot
app/match.py             classify each tile against data/icons/
app/vision.py            backend switch (icons | claude | ocr)
app/server.py            Flask routes
scripts/match_debug.py   annotate a screenshot with what the matcher sees
static/                  phone UI (no build step)
data/fusions.sample.json Dragon-only sample so the app runs before scraping
data/icons/              reference pictures (filled by the scraper; commit them)
tests/                   parser, merge, ranking, endpoint and tile/matcher tests
                         (tests/fixtures/shelf_desktop.png is a real shelf)
```
