#!/usr/bin/env python3
"""Draw what the matcher sees on a shelf screenshot.

    python scripts/match_debug.py shelf.png            # writes shelf.debug.png + prints a table
    python scripts/match_debug.py shelf.png --threshold 0.4

Green ring = confident, amber = low margin (runner-up close), red = below threshold.
Use it to tune MATCH_THRESHOLD, and to spot wiki pictures that don't match the
in-game art (a tile that keeps scoring low against the right animal needs a better
reference: drop a screenshot crop into data/icons/<Animal>__2.png).
"""
import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.match import ICON_DIR, IconMatcher  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--icons", default=str(ICON_DIR))
    ap.add_argument("--threshold", type=float, default=None)
    args = ap.parse_args()

    m = IconMatcher(Path(args.icons))
    print(f"{len(m.refs)} reference pictures for {len(m.names)} animals")
    data = Path(args.image).read_bytes()
    results, tiles = m.detect(data, **({"threshold": args.threshold} if args.threshold is not None else {}))
    img = cv2.imread(args.image)
    print(f"{len(tiles)} tiles found\n")
    print(f"{'#':>2}  {'best guess':<22}{'score':>6}  {'runner-up':<22}{'score':>6}")
    for i, r in enumerate(results):
        x, y, w, h = r.box
        colour = (0, 200, 0) if r.confident else (0, 170, 255) if r.name else (0, 0, 255)
        cv2.circle(img, (x + w // 2, y + h // 2), w // 2, colour, max(2, w // 30))
        label = f"{i}:{(r.name or r.top[0][0] if r.top else '?')} {r.score:.2f}"
        cv2.putText(img, label, (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, w / 180, colour, max(1, w // 60))
        print(f"{i:>2}  {(r.name or '-'):<22}{r.score:>6.2f}  {(r.runner_up or '-'):<22}{r.runner_score:>6.2f}"
              + ("" if r.confident else "   <-- check"))
    out = Path(args.image).with_suffix(".debug.png")
    cv2.imwrite(str(out), img)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
