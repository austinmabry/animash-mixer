"""End-to-end on the real shelf screenshot in tests/fixtures.

We do not ship the wiki icons, so references are manufactured from the
screenshot itself with deliberate differences (looser crop, blur, JPEG
round-trip, slight scale) to stand in for the wiki's own renders, plus
mirrored decoys under other names. The matcher must name all 12 tiles.
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.match import IconMatcher  # noqa: E402
from app.tiles import crop_tile, find_tiles  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "shelf_desktop.png"
# reading order, as a human labels the screenshot
LABELS = ["Gecko", "Binturong", "Lava", "Clouds", "Pumpkin", "Toaster", "Durian", "Grasshopper",
          "Bamboo", "Water", "Kitten", "Octopus"]


def test_find_tiles_real_screenshot():
    img = cv2.imread(str(FIX))
    tiles = find_tiles(img)
    assert len(tiles) == 12
    assert all(abs(t.r - 81) <= 3 for t in tiles)
    xs = sorted({round(t.cx / 50) for t in tiles})
    ys = sorted({round(t.cy / 50) for t in tiles})
    assert len(xs) == 4 and len(ys) == 3          # 4 columns × 3 rows
    assert tiles[0].cx < tiles[1].cx and tiles[3].cy < tiles[4].cy   # reading order


def _fake_reference(img, t, rng):
    """A stand-in for the wiki icon: different crop, blur, jpeg, alpha from white."""
    crop = crop_tile(img, t, inner=0.97)
    f = rng.uniform(0.7, 1.3)
    crop = cv2.resize(crop, None, fx=f, fy=f)
    crop = cv2.GaussianBlur(crop, (0, 0), 1.2)
    _, enc = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
    crop = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    h, w = crop.shape[:2]
    alpha = np.zeros((h, w), np.uint8)
    cv2.circle(alpha, (w // 2, h // 2), int(min(h, w) * 0.5), 255, -1)   # the game disc is the sprite's extent
    return np.dstack([crop, alpha])


def test_matcher_names_every_tile(tmp_path):
    rng = np.random.default_rng(1)
    img = cv2.imread(str(FIX))
    tiles = find_tiles(img)
    for name, t in zip(LABELS, tiles):
        ref = _fake_reference(img, t, rng)
        cv2.imwrite(str(tmp_path / f"{name}.png"), ref)
        # decoys: mirrored and hue-shifted versions of real tiles under other names
        cv2.imwrite(str(tmp_path / f"Decoy_{name}.png"), cv2.flip(ref, 1))
        hsv = cv2.cvtColor(ref[:, :, :3], cv2.COLOR_BGR2HSV)
        hsv[:, :, 0] = (hsv[:, :, 0].astype(int) + 60) % 180
        cv2.imwrite(str(tmp_path / f"Shifted_{name}.png"), np.dstack([cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR), ref[:, :, 3]]))

    m = IconMatcher(tmp_path)
    assert len(m.names) == 36
    t0 = time.time()
    results, _ = m.detect(FIX.read_bytes())
    dt = time.time() - t0
    got = [r.name for r in results]
    assert got == LABELS, [(r.name, round(r.score, 2), r.runner_up, round(r.runner_score, 2)) for r in results]
    assert all(r.confident for r in results), [(r.name, round(r.score - r.runner_score, 2)) for r in results]
    print(f"\n{dt:.1f}s for 12 tiles × 36 refs; scores "
          + ", ".join(f"{r.name}:{r.score:.2f}(+{r.score - r.runner_score:.2f})" for r in results))
