"""Fusion catalog: loads data/fusions.json and ranks combinations."""
from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "fusions.json"
SAMPLE = ROOT / "data" / "fusions.sample.json"


class Catalog:
    def __init__(self, path: Path | None = None):
        self.path = path or (DATA if DATA.exists() else SAMPLE)
        self.is_sample = self.path == SAMPLE
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.scraped_at = raw.get("scraped_at")
        self.animals: list[str] = list(raw["animals"])
        self._canon = {a.lower(): a for a in self.animals}
        self.by_pair: dict[frozenset, dict] = {}
        for f in raw["fusions"]:
            self.by_pair[frozenset((f["a"], f["b"]))] = f
            # animals referenced only as a parent still count as known
            for n in (f["a"], f["b"]):
                if n.lower() not in self._canon:
                    self._canon[n.lower()] = n
                    self.animals.append(n)
        self.animals.sort(key=str.lower)

    # -------------------------------------------------------------- helpers
    def canonical(self, name: str) -> str | None:
        return self._canon.get(name.strip().lower())

    def clean(self, names: list[str]) -> list[str]:
        out: list[str] = []
        for n in names:
            c = self.canonical(n)
            if c and c not in out:
                out.append(c)
        return out

    def best_mixes(self, available: list[str], top_n: int = 5) -> dict:
        avail = self.clean(available)
        found, missing = [], []
        for a, b in combinations(avail, 2):
            f = self.by_pair.get(frozenset((a, b)))
            if f:
                found.append({
                    "parents": [f["a"], f["b"]],
                    "name": f["name"],
                    "stars": f["stars"],
                    "tier": f.get("tier"),
                    "icon": f.get("icon"),
                })
            else:
                missing.append([a, b])
        found.sort(key=lambda m: (-m["stars"], m["name"] or ""))
        return {
            "available": avail,
            "mixes": found[:top_n],
            "total_known": len(found),
            "missing": missing,
        }
