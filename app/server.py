"""Flask app: serves the mobile UI and the JSON endpoints."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .fusions import Catalog
from .vision import identify

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
catalog = Catalog()

TOP_N = int(os.environ.get("TOP_N", "5"))


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "animals": len(catalog.animals),
        "fusions": len(catalog.by_pair),
        "scraped_at": catalog.scraped_at,
        "sample_data": catalog.is_sample,
        "vision_backend": os.environ.get("VISION_BACKEND", "claude"),
    })


@app.get("/api/animals")
def animals():
    return jsonify(catalog.animals)


@app.post("/api/mixes")
def mixes():
    body = request.get_json(silent=True) or {}
    names = body.get("animals") or []
    if not isinstance(names, list):
        return jsonify({"error": "animals must be a list of names"}), 400
    return jsonify(catalog.best_mixes(names, top_n=int(body.get("top_n") or TOP_N)))


@app.post("/api/analyze")
def analyze():
    f = request.files.get("image")
    if not f:
        return jsonify({"error": "Upload an image in the 'image' field."}), 400
    try:
        seen = identify(f.read(), catalog.animals)
    except Exception as e:  # surface the real reason to the UI
        return jsonify({"error": f"Could not read the picture: {e}"}), 502
    result = catalog.best_mixes(seen["animals"], top_n=TOP_N)
    result["backend"] = seen["backend"]
    if app.debug:
        result["raw"] = seen["raw"]
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=True)
