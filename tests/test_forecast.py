"""Forecast menu: reading tables, training with backtests, forecasting with a saved model, the Indian AQI, the API."""

import json

import numpy as np
import pandas as pd
import pytest

import webapp.workspace as ws
from lulc_fetch import forecast as fc
from lulc_fetch import openmeteo
from tests.helpers import ok, run


def _stations(n_hours=24 * 40, future=48, seed=0):
    """Hourly values at 5 stations: a daily cycle, a station offset, a weather input that matters, noise; plus `future`
    rows with only the weather (as a weather forecast)."""
    rng = np.random.default_rng(seed)
    t = pd.date_range("2026-01-01", periods=n_hours + future, freq="h")
    rows = []
    for i in range(5):
        temp = 25 + 5 * np.sin(2 * np.pi * (t.hour - 9) / 24) + rng.normal(0, 1, len(t))
        y = 60 + 10 * i + 20 * np.sin(2 * np.pi * t.hour / 24) - 2 * (temp - 25) + rng.normal(0, 2, len(t))
        for k in range(len(t)):
            rows.append({"station": f"S{i}", "lat": 12.9 + 0.02 * i, "lon": 77.5 + 0.03 * i, "time": t[k].strftime("%Y-%m-%dT%H:%M"),
                         "aqi": round(y[k], 2) if k < n_hours else None, "temperature": round(temp[k], 2)})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    p = tmp_path_factory.mktemp("fc") / "stations.csv"
    _stations().to_csv(p, index=False)
    return p


def test_describe_guesses_the_columns(table):
    d = fc.describe(table)
    assert d["guess"] == {"time": "time", "target": "aqi", "series": "station", "lat": "lat", "lon": "lon", "horizon": 48}
    assert d["freq"] == "h" and d["series_count"] == 5 and d["future_rows"] == 5 * 48
    assert "temperature" in d["inputs"]


def test_time_steps_and_day_first_dates(tmp_path):
    t = pd.date_range("2024-01-01", periods=400, freq="D")
    pd.DataFrame({"date": t.strftime("%d-%m-%Y"), "rain": np.arange(400.0)}).to_csv(tmp_path / "d.csv", index=False)
    d = fc.describe(tmp_path / "d.csv")
    assert d["freq"] == "D" and d["start"].startswith("2024-01-01") and d["end"].startswith("2025-02-03")   # 13-01 read as 13 January
    m = pd.date_range("2010-01-01", periods=120, freq="MS")
    pd.DataFrame({"month": m.strftime("%Y-%m"), "v": np.arange(120.0)}).to_csv(tmp_path / "m.csv", index=False)
    assert fc.describe(tmp_path / "m.csv")["freq"] == "MS"


def test_train_beats_baselines_and_forecasts(table, tmp_path):
    r = fc.train(table, tmp_path, time_col="time", target="aqi", series_col="station", lat_col="lat", lon_col="lon",
                 inputs=["temperature"], horizon=24, model="lightgbm", name="t")
    json.dumps(r, allow_nan=False)                          # goes to the browser as it is
    met, base = r["meta"]["metrics"], r["meta"]["baseline"]
    assert met["mae"] < 0.75 * base["mae"], (met, base)      # learns the cycle, the stations and the weather
    assert met["mae"] < 4                                     # noise is 2 (MAE of pure noise ≈ 1.6)
    assert r["meta"]["known_inputs"] and "came from the table" in r["warnings"][0]
    assert len(r["rows"]) == 5 * 24 and r["rows"][0]["time"] == "2026-02-10 00:00"
    for s in r["series"]:
        assert all(lo <= f <= hi for lo, f, hi in zip(s["lo"], s["fc"], s["hi"]))
    assert {x["name"] for x in r["importance"]} >= {"aqi: recent values", "Hour of day"}
    # the saved model forecasts again, further ahead, with a warning
    p = fc.predict(r["path"], table, horizon=36)
    assert len(p["rows"]) == 5 * 36 and any("checked 24 hours" in w for w in p["warnings"])
    assert np.allclose(p["series"][0]["fc"][:24], r["series"][0]["fc"], atol=1e-6)


def test_compare_all_ranks_every_model(table, tmp_path):
    r = fc.train(table, tmp_path, time_col="time", target="aqi", series_col="station", horizon=12, backtests=1, model="auto", name="all")
    keys = [c["key"] for c in r["compare"]]
    assert set(keys) >= {"linear", "rf", "hgb", "naive", "seasonal"} and r["meta"]["model"] == keys[0]
    maes = [c["mae"] for c in r["compare"]]
    assert maes == sorted(maes)


def test_direct_forecast_of_intermittent_rain(tmp_path):
    """Daily rain with dry and wet seasons: direct forecasting doesn't invent rain after a dry spell (step by step can
    feed its own small guesses back in), and the backtest windows are spread over the year."""
    rng = np.random.default_rng(1)
    t = pd.date_range("2018-01-01", "2023-12-31", freq="D")
    rows = []
    for i in range(6):
        wet = (t.month >= 6) & (t.month <= 9)
        rain = np.where(wet & (rng.random(len(t)) < 0.7), rng.gamma(2, 8, len(t)), 0) + np.where(~wet & (rng.random(len(t)) < 0.03), rng.gamma(1, 3, len(t)), 0)
        rows.append(pd.DataFrame({"date": t.strftime("%Y-%m-%d"), "point": f"p{i}", "lat": 10 + 0.25 * i, "lon": 76.0, "rain": rain.round(1)}))
    pd.concat(rows).to_csv(tmp_path / "rain.csv", index=False)
    assert fc._origins(2000, 7, "D", 4)[0] < 2000 - 7 - 200          # spread over the last year, not only December
    r = fc.train(tmp_path / "rain.csv", tmp_path, time_col="date", target="rain", series_col="point", horizon=7, model="lightgbm",
                 strategy="direct", backtests=4, name="rain")
    assert r["meta"]["strategy"] == "direct" and "direct" in r["model_title"]
    assert max(max(s["fc"]) for s in r["series"]) < 2               # 31 December, dry season: (almost) no rain
    assert any(n["name"] == "How far ahead" for n in r["importance"]) or r["importance"]
    p = fc.predict(r["path"], tmp_path / "rain.csv")
    assert np.allclose(p["series"][0]["fc"], r["series"][0]["fc"])


def test_settings_early_stopping_and_tuning(table, tmp_path):
    """Model settings are used and kept within range; early stopping keeps fewer trees; tuning tries settings with the
    same backtests and keeps the best (never worse than the user's)."""
    assert fc.settings("lightgbm", {"n_estimators": 99999, "learning_rate": "x"}) == {**fc.settings("lightgbm"), "n_estimators": 5000}
    assert fc.options({"band": 5, "early_stop": 1})["band"] == 50 and fc.options({"early_stop": 1})["early_stop"] is True
    r = fc.train(table, tmp_path, time_col="time", target="aqi", series_col="station", horizon=12, model="lightgbm", backtests=1,
                 strategy="recursive", params={"lightgbm": {"n_estimators": 2000, "learning_rate": 0.2}},
                 opts={"early_stop": True, "patience": 20, "band": 90, "tune": 2}, name="tuned")
    m = r["meta"]
    assert m["band_level"] == 90 and m["options"]["early_stop"] and m["rounds"] is not None and m["rounds"] < 2000
    assert len(m["tuning"]) == 3 and m["tuning"][0]["mae"] <= next(x["mae"] for x in m["tuning"] if x["trial"] == 0)
    assert abs(m["metrics"]["mae"] - m["tuning"][0]["mae"]) < 1e-9
    s = fc.schema()
    assert {p["name"] for p in s["models"]["xgboost"]["params"]} >= {"n_estimators", "learning_rate", "max_depth"} and s["options"]


def test_clear_messages(table, tmp_path):
    with pytest.raises(ValueError, match="choose a horizon of"):
        fc.train(table, tmp_path, time_col="time", target="aqi", series_col="station", horizon=500)
    with pytest.raises(ValueError, match="no column"):
        fc.train(table, tmp_path, time_col="time", target="nope")
    pd.DataFrame({"a": [1, 2]}).to_csv(tmp_path / "x.csv", index=False)
    with pytest.raises(ValueError, match="Not a forecasting model"):
        import joblib
        joblib.dump({"x": 1}, tmp_path / "x.joblib")
        fc.predict(tmp_path / "x.joblib", table)


def test_indian_aqi():
    assert openmeteo.sub_index("pm2_5", np.array([0, 30, 60, 90, 120, 250]))[1:].tolist() == [50, 100, 200, 300, 400]
    assert openmeteo.sub_index("pm10", np.array([75.0]))[0] == 75
    h = 48
    aqi, main = openmeteo.indian_aqi({"pm2_5": np.full(h, 45.0), "pm10": np.full(h, 50.0), "no2": np.full(h, 20.0), "o3": np.full(h, 30.0)})
    assert np.isnan(aqi[:23]).all()                       # needs a full 24-hour average
    assert aqi[30] == 75 and main[30] == "PM2.5"          # PM2.5 45 µg/m³ → 75, the highest sub-index
    aqi, _ = openmeteo.indian_aqi({"pm2_5": np.full(h, 45.0), "no2": np.full(h, 20.0)})
    assert np.isnan(aqi).all()                             # CPCB: at least three pollutants


def test_api_train_models_run(client, table):
    import shutil
    dest = ws.root() / "tables" / "fc_test_stations.csv"
    dest.parent.mkdir(exist_ok=True)
    shutil.copy(table, dest)
    d = ok(client.get("/api/forecast/describe", params={"path": "tables/fc_test_stations.csv"}))
    g = d["guess"]
    r = run(client, "/api/forecast/train", {"table": "tables/fc_test_stations.csv", "time_col": g["time"], "target": g["target"],
                                            "series_col": g["series"], "lat_col": g["lat"], "lon_col": g["lon"], "inputs": ["temperature"],
                                            "horizon": 12, "model": "hgb", "backtests": 1, "name": "api test"})
    assert r["path"].startswith("models/forecast/") and r["table"].startswith("tables/") and "rows" not in r
    models = ok(client.get("/api/forecast/models"))
    assert any(m["path"] == r["path"] for m in models)
    p = run(client, "/api/forecast/run", {"model": r["path"], "table": "tables/fc_test_stations.csv", "horizon": 6, "name": "api run"})
    assert p["horizon"] == 6 and len(p["series"]) == 5
    assert client.post("/api/forecast/run", json={"model": "models/nope.joblib", "table": "tables/fc_test_stations.csv"}).status_code == 404
