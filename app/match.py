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
SCALES = (0.80, 0.90, 1.00, 1.10, 1.22)     # reference sprite size relative to the crop
PAD = 0.18                                  # white margin around each reference (fraction of CROP)
THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.45"))
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
        self.refs: list[tuple] = []   # (animal, fine views per scale, coarse view)
        files = sorted(icon_dir.glob("*.png")) if icon_dir.exists() else []
        pad = int(CROP * PAD)
        for f in files:
            stem = f.stem.split("__")[0]
            name = by_file.get(stem, stem.replace("_", " "))
            img = cv2.imread(str(f), cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            sprite = self._on_white(img)
            views = [self.feat(cv2.copyMakeBorder(self._resize(sprite, int(CROP * sc)), pad, pad, pad, pad,
                                                  cv2.BORDER_CONSTANT, value=(255, 255, 255))) for sc in SCALES]
            pad_s = int(CROP_S * PAD)
            coarse = self.feat(cv2.copyMakeBorder(self._resize(sprite, CROP_S), pad_s, pad_s, pad_s, pad_s,
                                                  cv2.BORDER_CONSTANT, value=(255, 255, 255)))
            self.refs.append((name, views, coarse))
        self.names = sorted({r[0] for r in self.refs}, key=str.lower)

    @property
    def ready(self) -> bool:
        return bool(self.refs)

    # ------------------------------------------------------------ set-up
    @staticmethod
    def _on_white(img: np.ndarray) -> np.ndarray:
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        if img.shape[2] == 4:
            a = img[:, :, 3]
            ys, xs = np.where(a > 16)
            if len(xs):
                img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                a = img[:, :, 3]
            af = (a[:, :, None] / 255.0)
            bgr = (img[:, :, :3] * af + 255 * (1 - af)).astype(np.uint8)
        else:
            bgr = img[:, :, :3]
            # trim white margins so the sprite fills the reference like it fills a tile
            dark = cv2.inRange(bgr, (0, 0, 0), (235, 235, 235))
            ys, xs = np.where(dark > 0)
            if len(xs):
                bgr = bgr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = bgr.shape[:2]
        s = max(h, w)
        canvas = np.full((s, s, 3), 255, np.uint8)
        canvas[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = bgr
        return canvas

    @staticmethod
    def _resize(img: np.ndarray, size: int) -> np.ndarray:
        return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)

    @staticmethod
    def feat(bgr: np.ndarray) -> list[np.ndarray]:
        """Channels compared independently: lightness, the two Lab colour axes, edges.
        Plain RGB correlation is dominated by the sprite's silhouette against white;
        separate colour and edge votes tell an orange pumpkin from a green durian."""
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)          # 8-bit: matchTemplate is ~20x faster on uint8
        L = lab[:, :, 0]
        gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
        edge = np.clip(cv2.magnitude(gx, gy) / 4.0, 0, 255).astype(np.uint8)
        return [L, lab[:, :, 1], lab[:, :, 2], edge]

    # ----------------------------------------------------------- matching
    @staticmethod
    def disc_crop(bgr: np.ndarray, t: Tile) -> np.ndarray:
        """The whole disc as a CROP×CROP square with everything outside the disc white."""
        sq = crop_tile(bgr, t, inner=1.0)
        sq = cv2.resize(sq, (CROP, CROP), interpolation=cv2.INTER_AREA)
        mask = np.zeros((CROP, CROP), np.uint8)
        cv2.circle(mask, (CROP // 2, CROP // 2), int(CROP * 0.47), 255, -1)
        sq[mask == 0] = 255
        return sq

    def classify(self, crop: np.ndarray, top_n: int = 3) -> list[tuple[str, float]]:
        """crop: CROP×CROP disc crop (see disc_crop). Returns [(animal, score)] best first."""
        cf = self.feat(crop)
        cs = self.feat(self._resize(crop, CROP_S))

        def ncc(view, c) -> float:
            maps = [cv2.matchTemplate(v, ch, cv2.TM_CCOEFF_NORMED) for v, ch in zip(view, c)]
            return float(cv2.minMaxLoc(sum(maps) / len(maps))[1])

        # pass 1: every reference at low resolution; pass 2: full multi-scale for the best few
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as ex:
            first = list(ex.map(lambda ref: ncc(ref[2], cs), self.refs))
            order = sorted(range(len(self.refs)), key=lambda i: -first[i])[:SHORTLIST]
            full = list(ex.map(lambda i: max(ncc(v, cf) for v in self.refs[i][1]), order))
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
        for t in tiles:
            top = self.classify(self.disc_crop(bgr, t)) if self.ready else []
            name, sc = top[0] if top else (None, -1.0)
            r_name, r_sc = top[1] if len(top) > 1 else (None, -1.0)
            ok = name is not None and sc >= threshold
            results.append(TileResult(
                box=t.box, name=name if ok else None, score=sc,
                runner_up=r_name, runner_score=r_sc,
                confident=ok and (sc - r_sc) >= MIN_MARGIN, top=top,
            ))
        return results, tiles
