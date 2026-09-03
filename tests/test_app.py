import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.fusions import SAMPLE, Catalog  # noqa: E402
from app.vision import normalise, ocr_text_to_names, parse_name_list  # noqa: E402


def test_best_mixes_ranks_and_reports_missing():
    c = Catalog(SAMPLE)
    r = c.best_mixes(["dragon", "Black Hole", "camel", "Alien", "Bicycle", "Dragon", "NotAnAnimal"])
    assert r["available"] == ["Dragon", "Black Hole", "Camel", "Alien", "Bicycle"]
    top = r["mixes"]
    assert [m["name"] for m in top] == ["Wyrmhole", "Chainwyrm", "Zenodrake", "Sundragon"]
    assert top[0]["stars"] == 15 and top[0]["parents"] == ["Black Hole", "Dragon"]
    assert r["total_known"] == 4
    assert ["Black Hole", "Camel"] in r["missing"]  # sample data has Dragon pairs only


def test_top_n_and_order_independence():
    c = Catalog(SAMPLE)
    a = c.best_mixes(["Dragon", "Fire", "Ninja"], top_n=1)
    b = c.best_mixes(["Ninja", "Fire", "Dragon"], top_n=1)
    assert a["mixes"] == b["mixes"] and a["mixes"][0]["name"] == "Shadowfire"


def test_name_normalisation():
    known = ["Black Hole", "Dragon", "Ant Eater", "Ant"]
    assert normalise(["black hole", "Dragn", "Ant"], known, 80) == ["Black Hole", "Dragon", "Ant"]
    assert normalise(["Zebra"], known, 80) == []


def test_parse_name_list_tolerates_prose():
    assert parse_name_list('Sure! ```json\n["Dragon", "Ant"]\n```') == ["Dragon", "Ant"]
    assert parse_name_list("no json here") == []


def test_ocr_text_to_names():
    known = ["Black Hole", "Dragon", "Carton Of Milk", "Camel", "Ant"]
    text = "Dragon   Black Hole\nCarton Of MiIk  Camel\nLevel 3  Fuse"
    names = ocr_text_to_names(text, known)
    assert set(names) >= {"Dragon", "Black Hole", "Camel"}
    assert "Ant" not in names  # no bare 'Ant' token in the text


def test_api_endpoints():
    from app.server import app
    client = app.test_client()
    assert client.get("/api/health").get_json()["sample_data"] is True
    assert "Dragon" in client.get("/api/animals").get_json()
    r = client.post("/api/mixes", json={"animals": ["Dragon", "Plasma", "Owl"], "top_n": 5}).get_json()
    assert r["mixes"][0]["name"] == "Plasmagon"
    assert client.post("/api/analyze", data={}).status_code == 400
    assert client.get("/").status_code == 200
