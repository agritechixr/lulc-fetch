"""Classical ML (tabular data) on real tables: diabetes risk (classification, 16 models), rice production (regression),
and the Sentinel-2 pixels against ESA's scene classification."""

import pytest

import webapp.workspace as ws
from tests.helpers import ok, run

TOOL = "Classical ML (tabular data)"
MODELS = ["rf", "et", "xgb", "lgbm", "hgb", "dt", "svm", "sgd", "lr", "nb", "mlc", "mindist", "sam", "lda", "knn", "mlp"]
DIAB_CAT = ["gender", "city", "family_history_diabetes", "physical_activity_level", "diet_type", "smoking_status", "alcohol_consumption", "income_bracket"]
DIAB_NUM = ["age", "bmi", "hours_sleep_per_night", "stress_level", "fasting_blood_sugar", "hba1c_level", "blood_pressure_systolic",
            "blood_pressure_diastolic", "waist_circumference_cm"]
results: dict[str, dict] = {}


@pytest.mark.parametrize("model", MODELS)
def test_diabetes_risk(client, tables, model, report):
    r = run(client, "/api/ml/train", {"table": tables["diabetes_risk"], "target": "diabetes_risk", "features": DIAB_NUM + DIAB_CAT,
                                      "categorical": DIAB_CAT, "model": model, "name": f"diabetes_{model}"})
    results[model] = r
    report.metric(f"{model} accuracy", r["accuracy"])
    report.metric(f"{model} macro F1", r["f1_macro"])
    if model in ("rf", "xgb", "lgbm"):
        report.file(ws.root() / r["evaluation_html"], f"diabetes_{model}_evaluation.html", f"{r['model_title']}: evaluation report")
    assert r["accuracy"] > 0.6, f"{model}: {r['accuracy']:.3f} (always 'Low' would give 0.60)"


def test_diabetes_leaderboard(client, tables, report):
    rows = sorted(([m, r["model_title"], r["accuracy"], r["balanced_accuracy"], r["f1_macro"], r["kappa"], r["seconds"]] for m, r in results.items()),
                  key=lambda x: -x[2])
    report.table("Diabetes risk: test-set scores of every model", ["id", "model", "accuracy", "balanced acc.", "macro F1", "kappa", "seconds"], rows)
    cmp = run(client, "/api/ml/compare", {"table": tables["diabetes_risk"], "target": "diabetes_risk", "features": DIAB_NUM + DIAB_CAT,
                                          "categorical": DIAB_CAT, "max_rows": 6000})
    lb = cmp.get("leaderboard") or cmp.get("results") or []
    if lb and isinstance(lb, list) and isinstance(lb[0], dict):
        keys = [k for k in ("model", "title", "score", "accuracy", "f1_macro", "seconds") if k in lb[0]]
        report.table("Compare models (quick cross-validation)", keys, [[x.get(k) for k in keys] for x in lb])
    assert rows and rows[0][2] > 0.75   # best model ~0.79 on this data; always "Low" gives 0.60


def test_rice_production_regression(client, tables, report):
    d = ok(client.post("/api/tables/derive", json={"path": tables["India Agriculture Crop Production"], "q": "Crop = Rice", "name": "rice_ml"}))
    r = run(client, "/api/ml/train", {"table": d["path"], "target": "Production", "features": ["State", "Season", "Year", "Area"],
                                      "categorical": ["State", "Season", "Year"], "model": "lgbm", "task": "regression", "name": "rice_production",
                                      "common": {"target_transform": "log"}})
    report.metric("R²", r["r2"])
    report.metric("RMSE (t)", r["rmse"])
    report.file(ws.root() / r["evaluation_html"], "rice_production_evaluation.html", "LightGBM regression: evaluation report")
    assert r["task"] == "regression" and r["r2"] > 0.6


@pytest.mark.parametrize("model", ["rf", "lgbm", "svm", "mlc"])
def test_scene_pixels(client, s2_table, model, report):
    r = run(client, "/api/ml/train", {"table": s2_table["path"], "target": "label", "features": s2_table["features"], "model": model,
                                      "name": f"scl_{model}"})
    report.metric(f"{model} accuracy vs SCL", r["accuracy"])
    report.metric(f"{model} kappa", r["kappa"])
    assert r["accuracy"] > 0.85
