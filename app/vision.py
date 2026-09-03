"""Turn a screenshot of the Animash shelf into animal names.

Backends, chosen with VISION_BACKEND:
  icons   (default) - local: find the white tiles, match each against the
                      wiki reference pictures in data/icons/ (see match.py).
                      No key, no network. Needs the scraper to have run.
  claude            - sends the image to Claude with the list of animal
                      names. Weak for this game (tiles carry no text, so it
                      has to recognise the art from the name alone); kept as
                      a fallback while data/icons is empty.
  ocr               - Tesseract. Useless for the in-game shelf (no labels);
                      only here for screenshots that do show names.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re

from PIL import Image, ImageOps
from rapidfuzz import fuzz, process

MAX_EDGE = 1568  # keeps vision cost/latency down without losing legibility


def _prep(image_bytes: bytes) -> tuple[bytes, str]:
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def normalise(candidates: list[str], known: list[str], cutoff: int) -> list[str]:
    out: list[str] = []
    for c in candidates:
        if not isinstance(c, str) or not c.strip():
            continue
        m = process.extractOne(c.strip(), known, scorer=fuzz.ratio, score_cutoff=cutoff)
        if m and m[0] not in out:
            out.append(m[0])
    return out


def parse_name_list(text: str) -> list[str]:
    """Pull a JSON array of strings out of a model reply (tolerates fences/prose)."""
    m = re.search(r"\[.*?\]", text, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                return [x for x in arr if isinstance(x, str)]
        except json.JSONDecodeError:
            pass
    return re.findall(r'"([^"]+)"', text)


# ------------------------------------------------------------------ Claude
def identify_with_claude(image_bytes: bytes, known: list[str]) -> tuple[list[str], str]:
    import anthropic  # lazy so the OCR backend has no API dependency

    data, mime = _prep(image_bytes)
    prompt = (
        "This is a screenshot or photo from the game Animash showing the animals "
        "a player can currently fuse. Identify every animal shown. Use ONLY names "
        "from this list (exact spelling):\n\n"
        + ", ".join(known)
        + "\n\nRespond with a JSON array of the matching names and nothing else. "
        "Include an animal only if you can actually see it; do not guess."
    )
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    resp = client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content)
    return normalise(parse_name_list(text), known, cutoff=80), text


# --------------------------------------------------------------------- OCR
def identify_with_ocr(image_bytes: bytes, known: list[str]) -> tuple[list[str], str]:
    import pytesseract

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
    text = pytesseract.image_to_string(img)
    return ocr_text_to_names(text, known), text


def ocr_text_to_names(text: str, known: list[str]) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-']+", text)
    cands: set[str] = {l.strip() for l in text.splitlines() if l.strip()}
    for n in (1, 2, 3):  # multi-word names like "Black Hole", "Carton Of Milk"
        for i in range(len(words) - n + 1):
            cands.add(" ".join(words[i:i + n]))
    return normalise(sorted(cands), known, cutoff=88)


_matcher = None


def get_matcher(known: list[str]):
    global _matcher
    if _matcher is None:
        from .match import IconMatcher
        _matcher = IconMatcher(names=known)
    return _matcher


def identify_with_icons(image_bytes: bytes, known: list[str]) -> tuple[list[str], list[dict]]:
    m = get_matcher(known)
    results, _ = m.detect(image_bytes)
    tiles = [{
        "box": r.box, "name": r.name, "score": round(r.score, 3), "confident": r.confident,
        "alternatives": [{"name": n, "score": round(sc, 3)} for n, sc in r.top],
    } for r in results]
    names = [r.name for r in results if r.name]
    return names, tiles   # already canonical: icon files are named after catalog titles


def identify(image_bytes: bytes, known: list[str]) -> dict:
    backend = os.environ.get("VISION_BACKEND", "icons").lower()
    if backend == "icons":
        names, tiles = identify_with_icons(image_bytes, known)
        return {"backend": backend, "animals": names, "tiles": tiles, "raw": ""}
    if backend == "ocr":
        names, raw = identify_with_ocr(image_bytes, known)
    else:
        names, raw = identify_with_claude(image_bytes, known)
    return {"backend": backend, "animals": names, "tiles": [], "raw": raw}
