"""Classical ML (tabular data): every model trains, scores, compares and classifies an image."""

import numpy as np
import pytest
import rasterio

from tests.helpers import ok, run

MODELS = ["rf", "et", "xgb", "lgbm", "hgb", "dt", "svm", "sgd", "lr", "nb", "mlc", "mindist", "sam", "lda", "knn", "mlp"]


def test_schema_lists_every_model(client):
    s = ok(client.get("/api/ml/schema"))
    assert set(MODELS) <= set(s["models"])
    assert not s["unavailable"], f"models that can't be used: {s['unavailable']}"


def test_describe_table(client, table):
    d = ok(client.get("/api/tables/describe", params={"path": table["path"]}))
    assert d["rows"] == table["report"]["rows"]


@pytest.mark.parametrize("model", MODELS)
def test_train_every_model(client, table, home, model):
    r = run(client, "/api/ml/train", {"table": table["path"], "target": "label", "features": table["features"], "model": model, "name": f"m_{model}"})
    assert r["task"] == "classification"
    assert r["accuracy"] > 0.85, f"{model}: accuracy {r['accuracy']:.3f} on an easy 3-class problem"
    assert (home / r["path"]).is_file() and (home / r["evaluation_html"]).is_file()


def test_train_with_tuning_and_cross_validation(client, table):
    r = run(client, "/api/ml/train", {"table": table["path"], "target": "label", "features": table["features"], "model": "rf", "name": "tuned",
                                      "common": {"cv_folds": 3}, "tuning": {"enabled": True, "method": "random", "iter": 3, "folds": 3,
                                                                                  "space": {"n_estimators": "50, 100", "max_depth": "none, 10"}}})
    assert r["accuracy"] > 0.9 and r["tuning"]


@pytest.mark.parametrize("model", ["rf", "svm", "sgd", "mlp", "knn"])
def test_regression(client, table, model):
    """The target (NIR in DN, values in the thousands) is far from unit size: SVM / SGD / MLP must still fit it."""
    r = run(client, "/api/ml/train", {"table": table["path"], "target": "B08", "features": ["B02", "B03", "B04", "B11", "B12"],
                                      "model": model, "task": "regression", "name": f"nir_{model}"})
    assert r["task"] == "regression" and r["r2"] > 0.85, f"{model}: R² {r['r2']:.3f}"


def test_report_saved_to_a_folder(client, table, tmp_path):
    r = run(client, "/api/ml/train", {"table": table["path"], "target": "label", "features": table["features"], "model": "lr", "name": "copy",
                                      "report_dir": str(tmp_path)})
    assert list(tmp_path.glob("*.evaluation.html"))


def test_compare_models(client, table):
    r = run(client, "/api/ml/compare", {"table": table["path"], "target": "label", "features": table["features"], "max_rows": 900})
    assert r


def test_models_list_and_report(client, table):
    run(client, "/api/ml/train", {"table": table["path"], "target": "label", "features": table["features"], "model": "dt", "name": "listed"})
    models = ok(client.get("/api/models"))
    m = next(m for m in models if m["name"] == "listed")
    assert m["accuracy"] > 0.85
    assert client.get("/api/models/report", params={"path": m["path"]}).status_code == 200


def test_classify_an_image(client, table, data, s2_info, home):
    m = run(client, "/api/ml/train", {"table": table["path"], "target": "label", "features": table["features"], "model": "rf", "name": "mapper"})
    r = run(client, "/api/ml/predict", {"model": m["path"], "path": data["s2"], "band_map": s2_info["band_map"], "name": "rf_map"})
    with rasterio.open(home / r["path"]) as s, rasterio.open(data["folder"] / "labels.tif") as t:
        pred, truth = s.read(1), t.read(1)
        assert s.count == 2   # class + confidence
    assert (pred == truth).mean() > 0.95


def test_unknown_model(client, table):
    assert client.post("/api/ml/train", json={"table": table["path"], "target": "label", "features": ["B02"], "model": "nope"}).status_code == 400
