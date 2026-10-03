"""Agri menu: Diagnose crop disease (leaf photos) and Crop disease guide (the knowledge base).

The guide, the photo checks, the photo handling and the Hugging Face download (against a local test server) need nothing
extra. The diagnosis test needs the PyTorch add-on and a local copy of the disease models (the original .pth files): it runs
when LULC_AGRI_MODELS points to it, or the disease repo is at ~/Desktop/Farmer_ai, and is skipped otherwise."""

import io
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from lulc_fetch.agri import disease, knowledge
from lulc_fetch.agri.import_data import class_names, report
from lulc_fetch.agri.labels import label_display, match_crop
from tests.helpers import ok, run

MODELS = Path(os.environ.get("LULC_AGRI_MODELS", Path.home() / "Desktop" / "Farmer_ai"))
have_models = pytest.mark.skipif(not disease.find_models(MODELS)["crops"], reason=f"no disease models in {MODELS} (set LULC_AGRI_MODELS)")


def leafy(size=(320, 240), seed=0) -> Image.Image:
    """A textured green picture: passes the photo checks (bright enough, detailed, sharp)."""
    rng = np.random.default_rng(seed)
    a = np.zeros((size[1], size[0], 3), np.uint8)
    a[..., 1] = 120 + rng.integers(0, 100, a.shape[:2])
    a[..., 0] = 40 + rng.integers(0, 60, a.shape[:2])
    return Image.fromarray(a)


def save(img: Image.Image, path: Path, gps=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gps:
        ex = img.getexif()
        R = IFDRational
        lat, lon = gps
        dms = lambda v: (R(int(abs(v))), R(int(abs(v) * 60 % 60)), R(round(abs(v) * 3600 % 60, 2)))   # noqa: E731
        ex[0x8825] = {1: "N" if lat >= 0 else "S", 2: dms(lat), 3: "E" if lon >= 0 else "W", 4: dms(lon)}
        ex[306] = "2026:09:30 10:15:00"
        img.save(path, exif=ex)
    else:
        img.save(path)
    return path


# ------------------------------------------------------------------ data and names

def test_schema(client):
    s = ok(client.get("/api/agri/schema"))
    assert len(s["crops"]) == 42
    assert {"Mango", "Rice", "Tomato", "Potato", "pomrgranet"} <= set(s["crops"])
    for k, c in s["crops"].items():
        assert len(c["classes"]) == len(c["labels"]) >= 2, k
        assert 0.8 < c["accuracy"] <= 1, k
    assert s["detectors"]["original"]["crops"] == 16 and s["detectors"]["new"]["crops"] == 36
    assert s["recognised_only"] == ["Pepper", "Raspberry", "Sorghum", "Squash"]
    assert s["crops"]["Mulberry"]["kind"] == "variety" and s["crops"]["Rice"]["limited_kb"]
    assert "folder" in s["models"]


def test_friendly_names():
    assert label_display("Apple", "Apple___scab") == "Apple Scab"
    assert label_display("Banana", "Augmented Banana Moko Disease") == "Moko Disease"
    assert label_display("Tomato", "Yellow_leaf_curl_virus") == "Yellow Leaf Curl Virus"
    assert label_display("Mango", "Mango Healthy") == "Healthy"
    crops = knowledge.crops()
    assert match_crop("pomegranate", crops) == "pomrgranet"
    assert match_crop("Castard_apple", crops) == "Custard_apple"
    assert match_crop("okra", crops) == "okra" and match_crop("Pepper", crops) is None


def test_import_readers(tmp_path):
    """Both label formats and both test-report formats of the disease repository."""
    (tmp_path / "class_mapping.csv").write_text("Class,Index\nb,1\na,0\n")
    assert class_names(tmp_path) == ["a", "b"]
    (tmp_path / "class_names.txt").write_text("x\ny\n")
    assert class_names(tmp_path) == ["x", "y"]   # the .txt wins, as in the disease app
    (tmp_path / "convnext_report.txt").write_text("Test Accuracy: 97.50%\n\n            precision recall f1-score support\n\n"
                                                  "  Rust  0.9  0.8  0.85  10\n\n  accuracy  0.975  40\n  macro avg  0.9  0.9  0.91  40\n")
    r = report(tmp_path)
    assert r["accuracy"] == 0.975 and r["macro_f1"] == 0.91 and r["test_images"] == 40 and r["per_class"]["Rust"]["f1"] == 0.85


# ------------------------------------------------------------------ Crop disease guide

def test_guide_diseases(client):
    d = ok(client.get("/api/agri/guide/diseases", params={"crop": "Mango"}))["diseases"]
    model = [x for x in d if x["in_model"]]
    assert [x["label"] for x in model] == knowledge.crops()["Mango"]["classes"]
    anth = next(x for x in model if x["name"] == "Anthracnose")
    assert anth["records"] > 20 and anth["sections"]["management"] > 0   # the full guide: symptoms and treatment
    assert any(not x["in_model"] and x["records"] for x in d)              # plus pests and disorders the photos don't show
    assert client.get("/api/agri/guide/diseases", params={"crop": "Nope"}).status_code == 404


def test_guide_search(client):
    r = ok(client.get("/api/agri/guide/search", params={"crop": "Mango", "disease": "Powdery Mildew"}))
    assert r["total"] > 10 and all(x["disease"].lower() == "powdery mildew" or x.get("vision_label") == "Powdery Mildew" for x in r["records"])
    r = ok(client.get("/api/agri/guide/search", params={"crop": "Mango", "q": "anthracnose spray", "section": "management"}))
    assert r["total"] > 0 and set(r["sections"]) == {"management"}
    assert all("anthracnose" in (x["question"] + x["answer"] + x["disease"]).lower() for x in r["records"])
    assert ok(client.get("/api/agri/guide/search", params={"crop": "Mango", "q": "zzzz qqqq"}))["total"] == 0
    r = ok(client.get("/api/agri/guide/search", params={"crop": "Tomato", "disease": "Late Blight"}))   # symptom-only crop
    assert r["total"] >= 1 and set(r["sections"]) == {"symptoms"}


# ------------------------------------------------------------------ photos: checks, position, upload, folder, thumbnail

def test_photo_checks():
    th = knowledge.meta()["thresholds"]
    assert disease.check_quality(Image.new("RGB", (64, 64), "green")) == "too_small"
    assert disease.check_quality(Image.new("RGB", (300, 300), (10, 10, 10))) == "too_dark"
    assert disease.check_quality(Image.new("RGB", (300, 300), (120, 160, 90))) == "no_detail"
    ramp = np.repeat(np.linspace(40, 220, 320, dtype=np.uint8)[None, :, None], 240, 0).repeat(3, 2)   # contrast, but no sharp detail
    assert disease.check_quality(Image.fromarray(ramp)) == "blurry"
    assert disease.check_quality(leafy()) is None
    # soft problems refuse a photo only when the models are unsure; hard ones always
    assert disease.soft_ok("blurry", th, crop_conf=0.95) and not disease.soft_ok("blurry", th, crop_conf=0.7)
    assert not disease.soft_ok("too_dark", th, crop_conf=0.99) and disease.soft_ok(None, th)


def test_photo_position(tmp_path):
    p = save(leafy(), tmp_path / "geo.jpg", gps=(-19.1305, 72.9105))
    img, info = disease.open_photo(p)
    assert img.mode == "RGB" and abs(info["lat"] + 19.1305) < 1e-3 and abs(info["lon"] - 72.9105) < 1e-3
    assert info["taken"] == "2026-09-30 10:15:00"
    assert disease.open_photo(save(leafy(), tmp_path / "plain.png"))[1] == {}


def test_photo_upload_folder_thumbnail(client, tmp_path):
    buf = io.BytesIO()
    leafy().save(buf, "JPEG")
    up = ok(client.post("/api/agri/photos/upload", files=[("files", ("leaf.jpg", buf.getvalue(), "image/jpeg")),
                                                          ("files", ("notes.txt", b"not a photo", "text/plain"))]))
    assert [p["name"] for p in up["photos"]] == ["leaf.jpg"] and up["skipped"] == ["notes.txt"]
    for i in range(3):
        save(leafy(seed=i), tmp_path / ("sub" if i == 2 else "") / f"p{i}.png")
    (tmp_path / "readme.txt").write_text("x")
    assert len(ok(client.get("/api/agri/photos/folder", params={"path": str(tmp_path)}))["photos"]) == 2
    assert len(ok(client.get("/api/agri/photos/folder", params={"path": str(tmp_path), "recursive": True}))["photos"]) == 3
    r = client.get("/api/agri/photo", params={"path": up["photos"][0]["path"], "size": 64})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and max(Image.open(io.BytesIO(r.content)).size) <= 64
    assert client.get("/api/agri/photo", params={"path": str(tmp_path / "readme.txt")}).status_code == 404   # only photos are served


def test_models_folder_and_bad_requests(client, tmp_path):
    assert client.post("/api/agri/models", json={"folder": str(tmp_path)}).status_code == 400   # no models in it
    p = str(save(leafy(), tmp_path / "a.jpg"))
    assert client.post("/api/agri/diagnose", json={"photos": [p], "crop": "Nope"}).status_code == 400
    assert client.post("/api/agri/diagnose", json={"photos": []}).status_code == 422
    assert client.post("/api/agri/diagnose", json={"photos": [str(tmp_path / "missing.jpg")]}).status_code in (400, 404)


def test_models_folder_layouts(tmp_path):
    (tmp_path / "data" / "Mango").mkdir(parents=True)
    (tmp_path / "data" / "Mango" / "convnext_best.pth").write_bytes(b"x")
    (tmp_path / "master_model" / "new_crop_detector").mkdir(parents=True)
    (tmp_path / "master_model" / "crop_classifier_best.pth").write_bytes(b"x")
    for folder in (tmp_path, tmp_path / "data"):   # the repository folder or its data/ folder
        f = disease.find_models(folder)
        assert list(f["crops"]) == ["Mango"] and list(f["detectors"]) == ["original"]


# ------------------------------------------------------------------ diagnosis with the real models (when available)

@pytest.mark.dl
@have_models
def test_diagnose(client, tmp_path, report):
    ok(client.post("/api/agri/models", json={"folder": str(MODELS)}))
    photos = [save(leafy(seed=1), tmp_path / "leaf_gps.jpg", gps=(19.13, 72.91)),
              save(Image.new("RGB", (400, 300), (200, 200, 200)), tmp_path / "blank.jpg"),
              save(leafy(seed=2), tmp_path / "leaf.png")]
    # crop chosen and unclear photos allowed: every readable photo gets a diagnosis from the Potato model
    r = run(client, "/api/agri/diagnose", {"photos": [str(p) for p in photos], "crop": "Potato", "strict": False, "name": "potato test"})
    by = {p["file"]: p for p in r["photos"]}
    labels = knowledge.crops()["Potato"]["classes"]
    for f in ("leaf_gps.jpg", "leaf.png", "blank.jpg"):
        p = by[f]
        assert p["status"] in ("disease", "healthy") and p["crop"] == "Potato" and p["label"] in labels
        assert len(p["top"]) == 3 and abs(sum(t["conf"] for t in p["top"])) <= 1.0001 and p["top"][0]["conf"] >= p["top"][1]["conf"]
    assert "no_detail" in by["blank.jpg"]["warnings"]
    assert r["located"] == 1 and r["geojson"]["features"][0]["geometry"]["coordinates"] == [72.91, 19.13]
    table = ok(client.get("/api/tables/rows", params={"path": r["csv"], "limit": 10}))   # opens in the data viewer
    assert table["total"] == 3 and "diagnosis" in table["columns"]
    # crop detected and the photo check on: the blank photo is refused, textures aren't confidently a crop
    r = run(client, "/api/agri/diagnose", {"photos": [str(p) for p in photos] + [str(tmp_path / "moved.jpg")], "name": "auto test"})
    by = {p["file"]: p for p in r["photos"]}
    assert by["moved.jpg"]["status"] == "error" and r["counts"]["error"] == 1   # a photo moved since it was added
    assert by["blank.jpg"]["status"] == "retake" and by["blank.jpg"]["reason"] == "no_detail" and "crop_top" not in by["blank.jpg"]
    assert all(len(p.get("crop_top", [])) == 3 for f, p in by.items() if f not in ("blank.jpg", "moved.jpg"))
    report.metric("seconds for 3 photos (crop detected)", r["seconds"])


def test_hub_download(tmp_path, monkeypatch):
    """Published models are downloaded once, checked against index.json, and found in the download folder afterwards."""
    import hashlib
    import http.server
    import json as _json
    import threading
    from functools import partial

    pub = tmp_path / "published"
    files = {"Tomato/config.json": b'{"classes": ["a", "b"]}', "Tomato/model.safetensors": os.urandom(300_000)}
    for rel, data in files.items():
        (pub / rel).parent.mkdir(parents=True, exist_ok=True)
        (pub / rel).write_bytes(data)
    index = {rel: {"bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()} for rel, d in files.items()}
    (pub / "index.json").write_text(_json.dumps({"files": index}))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(http.server.SimpleHTTPRequestHandler, directory=str(pub)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(disease, "HUB_URL", f"http://127.0.0.1:{srv.server_address[1]}/")
    try:
        root = tmp_path / "cache"
        target = Path(disease.hub_models(root)["crops"]["Tomato"])
        disease.download(target, root, "Tomato", disease._hub_index(root))
        assert target.read_bytes() == files["Tomato/model.safetensors"]
        assert disease.find_models(root)["crops"] == {"Tomato": str(target)}
        assert disease._classes("Tomato", target) == ["a", "b"]   # labels from the published config.json
        disease.download(target, root, "Tomato", index)            # already there: nothing to do
        target.unlink()
        bad = {**index, "Tomato/model.safetensors": {**index["Tomato/model.safetensors"], "sha256": "0" * 64}}
        with pytest.raises(RuntimeError, match="damaged"):
            disease.download(target, root, "Tomato", bad)
        assert not target.exists() and not list(root.rglob("*.part"))
    finally:
        srv.shutdown()
