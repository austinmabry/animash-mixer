"""Find the animal tiles in an Animash shelf screenshot.

The game draws every selectable animal inside a white circular tile on a
dark panel, so tile detection is a bright-blob search: threshold near-white
pixels, close the holes the artwork punches in each disc, take contours,
and keep the ones that are round and share a common radius (tiles in a grid
are all the same size). No template search, no scale guessing.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Tile:
    cx: int
    cy: int
    r: int
    fill: float      # how round the blob is (contour area / circle area)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.cx - self.r, self.cy - self.r, 2 * self.r, 2 * self.r


def find_tiles(bgr: np.ndarray, min_r_frac: float = 0.015, max_r_frac: float = 0.12) -> list[Tile]:
    H, W = bgr.shape[:2]
    short = min(H, W)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 190), (180, 60, 255))
    # fill the artwork inside each disc so the blob is solid
    k = max(3, int(short * 0.012)) | 1
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    cands: list[Tile] = []
    for c in contours:
        (x, y), r = cv2.minEnclosingCircle(c)
        if not (short * min_r_frac <= r <= short * max_r_frac):
            continue
        area = cv2.contourArea(c)
        fill = area / (np.pi * r * r)
        if fill < 0.6:
            continue
        cands.append(Tile(int(round(x)), int(round(y)), int(round(r)), float(fill)))
    if not cands:
        return []

    # tiles in a grid share a radius: learn it from the clean discs
    radii = np.array([t.r for t in cands], float)
    med = float(np.median(radii))
    tiles = [t for t in cands if abs(t.r - med) <= 0.2 * med]

    # discs whose artwork reaches the rim break into arcs and fail the round test;
    # a Hough search locked to the learned radius recovers them
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (0, 0), max(1.0, med / 40))
    circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=med * 1.6,
                               param1=120, param2=28, minRadius=int(med * 0.85), maxRadius=int(med * 1.15))
    if circles is not None:
        for x, y, r in circles[0]:
            if all((x - t.cx) ** 2 + (y - t.cy) ** 2 > (med * 0.8) ** 2 for t in tiles):
                # accept only if the disc interior is mostly bright (rejects dark circular UI)
                disc = np.zeros_like(gray)
                cv2.circle(disc, (int(x), int(y)), int(med * 0.9), 255, -1)
                if cv2.mean(white, mask=disc)[0] > 255 * 0.2:
                    tiles.append(Tile(int(round(x)), int(round(y)), int(round(med)), 0.0))
    tiles.sort(key=lambda t: (round(t.cy / med), t.cx))   # reading order
    return tiles


def crop_tile(bgr: np.ndarray, t: Tile, inner: float = 0.86) -> np.ndarray:
    """Square crop inside the disc (the art sits inside a thin grey ring)."""
    s = int(t.r * inner)
    H, W = bgr.shape[:2]
    x0, y0 = max(0, t.cx - s), max(0, t.cy - s)
    x1, y1 = min(W, t.cx + s), min(H, t.cy + s)
    return bgr[y0:y1, x0:x1].copy()
