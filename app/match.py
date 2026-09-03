"""Identify the animal in each shelf tile by comparing the tile crop with the
wiki's reference icons (data/icons/*.png).

Pipeline (see tiles.py for step 1):
  1. find the white circular tiles and crop the square inside each disc
  2. every reference icon is composited on white (the tile background),
     trimmed to its sprite, and kept at several sizes
  3. for each crop, run TM_CCOEFF_NORMED for every icon at every size and
     take the best score per animal; the winner is reported with its margin
     over the runner-up so the UI can flag doubtful tiles

Reference files are named after the animal: data/icons/Black_Hole.png.
Extra views of the same animal go in Black_Hole__2.png, Black_Hole__3.png...
All of an animal's references compete and the best one counts.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .tiles import Tile, crop_tile, find_tiles

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "data" / "icons"

CROP = 80                                   # tile crops are resized to CROP×CROP (fine pass)
CROP_S = 32                                 # coarse pass size
SCALES = (0.90, 1.05, 1.20, 1.35, 1.50)     # sprite size relative to the disc (the game draws them larger than the disc)
COARSE_SCALES = (1.0, 1.3)                  # sizes tried in the cheap first pass
PAD = 0.18                                  # white margin around each reference (fraction of CROP)
THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.45"))
REF_MAX_EDGE = 320                          # reference pictures are downscaled to this before keying
WHITE_TOL = 14                              # flood-fill tolerance when keying out the white background
COLOUR_SD0 = 6.0                            # colour-channel std at which colour gets half weight
SHORTLIST = 12                              # refs that get the full multi-scale pass
MIN_MARGIN = 0.04                           # winner must beat runner-up by this


def safe_name(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", title)


@dataclass
class TileResult:
    box: tuple[int, int, int, int]
    name: str | None            # best guess (None if nothing cleared the threshold)
    score: float
    runner_up: str | None
    runner_score: float
    confident: bool
    top: list[tuple[str, float]] = field(default_factory=list)


class IconMatcher:
    def __init__(self, icon_dir: Path = ICON_DIR, names: list[str] | None = None):
        by_file = {safe_name(n): n for n in (names or [])}
        self.refs: list[tuple[str, np.ndarray]] = []   # (animal, square RGBA sprite)
        files = sorted(icon_dir.glob("*.png")) if icon_dir.exists() else []
        for f in files:
            stem = f.stem.split("__")[0]
            name = by_file.get(stem, stem.replace("_", " "))
            img = cv2.imread(str(f), cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            self.refs.append((name, self._sprite(img)))
        self._views: dict[tuple, list] = {}   # background signature -> per-ref views
        self.names = sorted({r[0] for r in self.refs}, key=str.lower)

    @property
    def ready(self) -> bool:
        return bool(self.refs)

    # ------------------------------------------------------------ set-up
    @staticmethod
    def _strip_cyan_band(img: np.ndarray) -> np.ndarray:
        """Nearly every wiki picture carries a light-blue wedge along its bottom edge
        (a capture artefact); the game tiles don't. Blank it out if it is a thin band."""
        if img.shape[2] < 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        hsv = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2HSV)
        cyan = (cv2.inRange(hsv, (90, 80, 150), (110, 255, 255)) > 0) & (img[:, :, 3] > 128)
        rows = cyan.mean(axis=1)
        H = img.shape[0]
        top = H
        while top > 0 and rows[top - 1] > 0.01:
            top -= 1
        if 0 < H - top <= int(H * 0.16):
            img = img.copy()
            img[top:, :, 3] = 0
        return img

    @staticmethod
    def _sprite(img: np.ndarray) -> np.ndarray:
        """Square RGBA sprite, trimmed to its opaque bounding box."""
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        if max(img.shape[:2]) > REF_MAX_EDGE:   # we only ever use them small; keying is far cheaper here
            f = REF_MAX_EDGE / max(img.shape[:2])
            img = cv2.resize(img, (max(1, int(img.shape[1] * f)), max(1, int(img.shape[0] * f))), interpolation=cv2.INTER_AREA)
        if img.shape[2] == 3:   # jpeg on white: treat near-white as transparent
            dark = cv2.inRange(img, (0, 0, 0), (235, 235, 235))
            img = np.dstack([img, dark])
        img = IconMatcher._key_out_white(img)
        img = IconMatcher._strip_cyan_band(img)
        a = img[:, :, 3]
        ys, xs = np.where(a > 16)
        if len(xs):
            img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = img.shape[:2]
        sz = max(h, w)
        canvas = np.zeros((sz, sz, 4), np.uint8)
        canvas[(sz - h) // 2:(sz - h) // 2 + h, (sz - w) // 2:(sz - w) // 2 + w] = img
        return canvas

    @staticmethod
    def _key_out_white(img: np.ndarray) -> np.ndarray:
        """The wiki pictures are opaque with a white background baked in. Make the
        background transparent by flood-filling near-white from the border, so
        white parts *inside* the sprite (cloud, toaster highlights) survive."""
        if (img[:, :, 3] > 200).mean() < 0.95:
            return img                      # already has real transparency
        bgr = img[:, :, :3]
        H, W = bgr.shape[:2]
        mask = np.zeros((H + 2, W + 2), np.uint8)
        filled = bgr.copy()
        tol = (WHITE_TOL,) * 3
        border = [(x, 0) for x in range(0, W, 8)] + [(x, H - 1) for x in range(0, W, 8)] + \
                 [(0, y) for y in range(0, H, 8)] + [(W - 1, y) for y in range(0, H, 8)]
        for x, y in border:
            if mask[y + 1, x + 1] == 0 and img[y, x, 3] > 0 and bgr[y, x].min() >= 255 - WHITE_TOL:
                cv2.floodFill(filled, mask, (x, y), (0, 0, 0), tol, tol, cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | 4 | (255 << 8))
        bg = mask[1:-1, 1:-1] > 0
        out = img.copy()
        out[bg, 3] = 0
        # soften the cut edge one pixel so anti-aliased outlines don't ring
        out[:, :, 3] = cv2.erode(out[:, :, 3], np.ones((3, 3), np.uint8))
        return out

    @staticmethod
    def composite(sprite: np.ndarray, size: int, bg_rows: np.ndarray, canvas: int) -> np.ndarray:
        """Sprite resized to `size`, centred on a canvas×canvas image whose rows are
        painted with `bg_rows` (the tile's vertical background gradient)."""
        out = np.empty((canvas, canvas, 3), np.uint8)
        idx = np.clip(np.arange(canvas) - (canvas - CROP) // 2, 0, CROP - 1)
        out[:] = bg_rows[idx][:, None, :]
        sp = cv2.resize(sprite, (size, size), interpolation=cv2.INTER_AREA)
        y0 = x0 = (canvas - size) // 2
        y1, x1 = y0 + size, x0 + size
        oy0, ox0 = max(0, -y0), max(0, -x0)
        y0, x0 = max(0, y0), max(0, x0)
        y1, x1 = min(canvas, y1), min(canvas, x1)
        patch = sp[oy0:oy0 + (y1 - y0), ox0:ox0 + (x1 - x0)]
        a = patch[:, :, 3:4] / 255.0
        out[y0:y1, x0:x1] = (patch[:, :, :3] * a + out[y0:y1, x0:x1] * (1 - a)).astype(np.uint8)
        return out

    def _views_for(self, bg_rows: np.ndarray) -> list:
        """Per-reference (fine views, coarse view) on this background, cached."""
        key = tuple((bg_rows[::8].astype(int) // 12).reshape(-1).tolist())   # same UI -> same key
        views = self._views.get(key)
        if views is None:
            pad = int(CROP * PAD)
            small_rows = cv2.resize(bg_rows[:, None, :], (1, CROP_S), interpolation=cv2.INTER_AREA)[:, 0, :]
            views = []
            for _, sprite in self.refs:
                fine = [self.feat(self.composite(sprite, int(CROP * sc), bg_rows, CROP + 2 * pad)) for sc in SCALES]
                coarse = [self.feat(self.composite_small(sprite, small_rows, sc)) for sc in COARSE_SCALES]
                views.append((fine, coarse, self.weights(fine[len(SCALES) // 2])))
            if len(self._views) > 8:
                self._views.clear()
            self._views[key] = views
        return views

    @staticmethod
    def composite_small(sprite: np.ndarray, bg_rows_small: np.ndarray, scale: float = 1.0) -> np.ndarray:
        pad = int(CROP_S * PAD)
        canvas = CROP_S + 2 * pad
        out = np.empty((canvas, canvas, 3), np.uint8)
        idx = np.clip(np.arange(canvas) - pad, 0, CROP_S - 1)
        out[:] = bg_rows_small[idx][:, None, :]
        size = int(round(CROP_S * scale))
        sp = cv2.resize(sprite, (size, size), interpolation=cv2.INTER_AREA)
        y0 = x0 = (canvas - size) // 2
        oy0, ox0 = max(0, -y0), max(0, -x0)
        y0, x0 = max(0, y0), max(0, x0)
        y1, x1 = min(canvas, y0 + size - oy0), min(canvas, x0 + size - ox0)
        patch = sp[oy0:oy0 + (y1 - y0), ox0:ox0 + (x1 - x0)]
        a = patch[:, :, 3:4] / 255.0
        out[y0:y1, x0:x1] = (patch[:, :, :3] * a + out[y0:y1, x0:x1] * (1 - a)).astype(np.uint8)
        return out

    @staticmethod
    def _resize(img: np.ndarray, size: int) -> np.ndarray:
        return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)

    @staticmethod
    def feat(bgr: np.ndarray) -> list[np.ndarray]:
        """Channels compared independently: lightness, the two Lab colour axes, edges.
        Plain RGB correlation is dominated by the sprite's silhouette against the
        background; separate colour and edge votes tell an orange pumpkin from a
        green durian."""
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)          # 8-bit: matchTemplate is ~20x faster on uint8
        L = lab[:, :, 0]
        gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
        edge = np.clip(cv2.magnitude(gx, gy) / 4.0, 0, 255).astype(np.uint8)
        return [L, lab[:, :, 1], lab[:, :, 2], edge]

    @staticmethod
    def weights(chans: list[np.ndarray]) -> np.ndarray:
        """How much each channel should count for this reference. Lightness and
        edges always count; the two colour axes count in proportion to how much
        colour the sprite has, so grey objects aren't judged on colour noise."""
        w = np.ones(4, np.float32)
        for i in (1, 2):
            sd = float(chans[i].std())
            w[i] = sd / (sd + COLOUR_SD0)
        return w / w.sum()

    @staticmethod
    def disc_crop(bgr: np.ndarray, t: Tile) -> np.ndarray:
        """The whole disc as a CROP×CROP square (corners outside the disc are junk;
        they get painted with the estimated background in detect())."""
        return cv2.resize(crop_tile(bgr, t, inner=1.0), (CROP, CROP), interpolation=cv2.INTER_AREA)

    @staticmethod
    def background_rows(crops: list[np.ndarray]) -> np.ndarray:
        """Per-row background colour of a tile (the game paints a vertical gradient
        inside each disc). Sampled from the rim of every disc, median across tiles."""
        yy, xx = np.mgrid[0:CROP, 0:CROP]
        r = np.hypot(yy - CROP / 2 + 0.5, xx - CROP / 2 + 0.5) / (CROP / 2)
        rim = (r > 0.80) & (r < 0.94)
        rows = np.zeros((CROP, 3), np.float32)
        for y in range(CROP):
            samples = np.concatenate([c[y][rim[y]] for c in crops]) if rim[y].any() else np.empty((0, 3))
            rows[y] = np.median(samples, axis=0) if len(samples) else np.nan
        # rows with no rim pixels (top/bottom few): copy nearest measured row
        good = np.where(~np.isnan(rows[:, 0]))[0]
        if not len(good):
            return np.full((CROP, 3), 255, np.uint8)
        nearest = good[np.abs(good[None, :] - np.arange(CROP)[:, None]).argmin(axis=1)]
        return np.clip(rows[nearest], 0, 255).astype(np.uint8)

    @staticmethod
    def paint_outside(crop: np.ndarray, bg_rows: np.ndarray) -> np.ndarray:
        yy, xx = np.mgrid[0:CROP, 0:CROP]
        r = np.hypot(yy - CROP / 2 + 0.5, xx - CROP / 2 + 0.5) / (CROP / 2)
        out = crop.copy()
        out[r >= 0.94] = np.broadcast_to(bg_rows[:, None, :], (CROP, CROP, 3))[r >= 0.94]
        return out

    def classify(self, crop: np.ndarray, views: list, top_n: int = 3) -> list[tuple[str, float]]:
        """crop: CROP×CROP tile with corners painted. Returns [(animal, score)] best first."""
        cf = self.feat(crop)
        cs = self.feat(self._resize(crop, CROP_S))

        def ncc(view, c, w) -> float:
            maps = [cv2.matchTemplate(v, ch, cv2.TM_CCOEFF_NORMED) * wi for v, ch, wi in zip(view, c, w)]
            return float(cv2.minMaxLoc(sum(maps))[1])

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as ex:
            first = list(ex.map(lambda v: max(ncc(cv, cs, v[2]) for cv in v[1]), views))
            order = sorted(range(len(views)), key=lambda i: -first[i])[:SHORTLIST]
            full = list(ex.map(lambda i: max(ncc(v, cf, views[i][2]) for v in views[i][0]), order))
        best: dict[str, float] = {}
        for i, sc in zip(order, full):
            name = self.refs[i][0]
            if sc > best.get(name, -2.0):
                best[name] = sc
        return sorted(best.items(), key=lambda kv: -kv[1])[:top_n]

    def detect(self, image_bytes: bytes, threshold: float = THRESHOLD) -> tuple[list[TileResult], list[Tile]]:
        bgr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            return [], []
        tiles = find_tiles(bgr)
        results: list[TileResult] = []
        if not tiles:
            return [], []
        crops = [self.disc_crop(bgr, t) for t in tiles]
        bg = self.background_rows(crops)
        views = self._views_for(bg) if self.ready else []
        for t, crop in zip(tiles, crops):
            top = self.classify(self.paint_outside(crop, bg), views) if self.ready else []
            name, sc = top[0] if top else (None, -1.0)
            r_name, r_sc = top[1] if len(top) > 1 else (None, -1.0)
            ok = name is not None and sc >= threshold
            results.append(TileResult(
                box=t.box, name=name if ok else None, score=sc,
                runner_up=r_name, runner_score=r_sc,
                confident=ok and (sc - r_sc) >= MIN_MARGIN, top=top,
            ))
        return results, tiles
