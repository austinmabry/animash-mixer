import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.scrape_wiki import merge, parse_fusion_table  # noqa: E402

HTML = (Path(__file__).parent / "fixtures" / "dragon_snippet.html").read_text()


def test_parse_rows():
    rows = parse_fusion_table(HTML, "Dragon")
    assert [r["b"] for r in rows] == ["Alien", "Black Hole", "Camel"]
    alien = rows[0]
    assert alien == {"a": "Dragon", "b": "Alien", "name": "Zenodrake", "stars": 7, "tier": "Legendary",
                     "icon": "https://static.wikia.nocookie.net/the-unofficial-animash/images/7/78/Zenodrake.png/revision/latest"}
    assert rows[1]["stars"] == 15 and rows[1]["tier"] == "Kappa Supreme"
    assert rows[1]["icon"].endswith("/Wyrmhole.png/revision/latest")
    assert rows[2]["icon"] is None


def test_merge_symmetric_and_conflicts():
    animals = ["Alien", "Black Hole", "Dragon", "Camel"]
    rows = [
        {"a": "Dragon", "b": "Alien", "name": "Zenodrake", "stars": 7, "tier": "Legendary", "icon": None},
        {"a": "Alien", "b": "Dragon", "name": "Zenodrake", "stars": 7, "tier": "Legendary", "icon": "http://x/z.png"},
        {"a": "Dragon", "b": "black hole", "name": "Wyrmhole", "stars": 15, "tier": "Kappa Supreme", "icon": None},
        {"a": "Black Hole", "b": "Dragon", "name": "Wyrmhole", "stars": 14, "tier": "Theta Supreme", "icon": None},
        {"a": "Camel", "b": "Sea Bunny", "name": "Bunnel", "stars": 4, "tier": "Unique", "icon": None},
    ]
    m = merge(rows, animals)
    keys = {(f["a"], f["b"]) for f in m["fusions"]}
    assert ("Alien", "Dragon") in keys and ("Black Hole", "Dragon") in keys
    z = next(f for f in m["fusions"] if f["name"] == "Zenodrake")
    assert z["icon"] == "http://x/z.png" and sorted(z["seen_on"]) == ["Alien", "Dragon"]
    assert len(m["conflicts"]) == 1 and m["conflicts"][0]["pair"] == ["Black Hole", "Dragon"]
    assert m["unmatched_parents"] == ["Sea Bunny"]
    assert m["fusions"][0]["stars"] == 15  # sorted best-first


def test_parse_all_tables_and_label_variants():
    from scraper.scrape_wiki import parse_stars
    html = (Path(__file__).parent / "fixtures" / "multi_table.html").read_text()
    rows = parse_fusion_table(html, "Shiba Inu")
    got = {r["b"]: (r["name"], r["stars"], r["tier"]) for r in rows}
    assert got == {
        "Alien": ("Shibalien", 5, "Rare"),
        "Cheetah": ("Shibeetah", 7, "Legendary"),      # listed twice, kept once
        "Pumpkin": ("Shibakin", 8, "Mythical"),        # second table
        "Toaster": ("Shibatoast", 7, None),            # bold-td headers, "7 Stars"
        "Rock": ("Shibrock", 4, "Unique"),             # tier name only
    }
    assert parse_stars("★★★★★") == (5, None)
    assert parse_stars("Kappa Supreme (15)") == (15, "Kappa Supreme")
    assert parse_stars("garbage") == (None, None)
