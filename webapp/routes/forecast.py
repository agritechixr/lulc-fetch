"""Forecast menu: get AQI & weather history for points (Open-Meteo), train a forecasting model on a table, and forecast
with a saved one. The science is in lulc_fetch/forecast.py and lulc_fetch/openmeteo.py. Shared helpers come from
webapp/core.py. Models are kept in models/forecast/ (apart from the Classical ML models)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import core
from .. import workspace as ws

router = APIRouter()
FC_DIR = ws.Dir("models/forecast")


def _fc_model(rel: str):
    p = core.model_path(rel)
    if p.parent.resolve() != FC_DIR.resolve():
        raise HTTPException(404, "No such forecasting model")
    return p


def _save_table(rep: dict, name: str) -> dict:
    """The forecast rows → a CSV in tables/ (opens in the data viewer); the rows themselves don't go to the browser."""
    from lulc_fetch import forecast
    core.TABLE_DIR.mkdir(exist_ok=True)
    out = core.unique(core.TABLE_DIR.path, f"{core.safe_stem(name, 'forecast')}.csv")
    forecast.save_rows(rep.pop("rows"), out)
    rep["table"] = ws.rel(out)
    rep["table_rows"] = len(rep["series"]) * rep["horizon"]
    return rep


@router.get("/api/forecast/schema")
def fc_schema():
    from lulc_fetch import forecast
    return forecast.schema()


@router.get("/api/forecast/describe")
def fc_describe(path: str):
    from lulc_fetch import forecast
    try:
        return forecast.describe(core.table_path(path))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/forecast/models")
def fc_models():
    from lulc_fetch import forecast
    if not FC_DIR.path.exists():
        return []
    return [{**m, "path": ws.rel(m["path"])} for m in forecast.list_models(FC_DIR.path)]


class FcTrain(BaseModel):
    table: str
    time_col: str = Field(max_length=200)
    target: str = Field(max_length=200)
    series_col: str | None = Field(None, max_length=200)
    lat_col: str | None = Field(None, max_length=200)
    lon_col: str | None = Field(None, max_length=200)
    inputs: list[str] = Field(default_factory=list, max_length=100)
    freq: str = Field("auto", pattern="^(auto|h|D|W|MS)$")
    horizon: int | None = Field(None, ge=1, le=5000)
    model: str = Field("auto", pattern="^(auto|lightgbm|xgboost|hgb|rf|linear|seasonal|naive)$")
    backtests: int = Field(3, ge=1, le=10)
    future_inputs: str = Field("auto", pattern="^(auto|known|repeat)$")
    strategy: str = Field("auto", pattern="^(auto|recursive|direct)$")
    params: dict = Field(default_factory=dict)    # each model's settings: {"lightgbm": {"n_estimators": 800, …}} (checked in forecast.settings)
    options: dict = Field(default_factory=dict)   # training options: fit_rows, early_stop, patience, band, seed, tune
    name: str = Field("forecast", max_length=80)


@router.post("/api/forecast/train")
def fc_train(req: FcTrain):
    from lulc_fetch import forecast
    table = core.table_path(req.table)

    def run(job):
        FC_DIR.mkdir(parents=True, exist_ok=True)
        stem = core.unique(FC_DIR.path, f"{core.safe_stem(req.name, 'forecast')}.joblib").stem
        rep = forecast.train(table, FC_DIR.path, time_col=req.time_col, target=req.target, series_col=req.series_col,
                             lat_col=req.lat_col, lon_col=req.lon_col, inputs=req.inputs, freq=req.freq, horizon=req.horizon,
                             model=req.model, backtests=req.backtests, future_inputs=req.future_inputs, strategy=req.strategy,
                             params=req.params, opts=req.options, name=stem)
        rep["path"] = ws.rel(rep["path"])
        return _save_table(rep, f"{stem}_forecast")

    title = f"Train forecast · {req.target}{f' by {req.series_col}' if req.series_col else ''}"
    return core.jobs.submit("fctrain", title, {"target": req.target, "model": req.model}, run).to_dict()


class FcRun(BaseModel):
    model: str
    table: str
    horizon: int | None = Field(None, ge=1, le=5000)
    name: str = Field("forecast", max_length=80)


@router.post("/api/forecast/run")
def fc_run(req: FcRun):
    from lulc_fetch import forecast
    model, table = _fc_model(req.model), core.table_path(req.table)

    def run(job):
        return _save_table(forecast.predict(model, table, horizon=req.horizon), req.name)

    return core.jobs.submit("forecast", f"Forecast · {model.stem}", {"model": model.stem}, run).to_dict()


class FcData(BaseModel):
    points: list[dict] = Field(min_length=1, max_length=500)   # {name, lat, lon}
    past_days: int = Field(60, ge=1, le=92)
    forecast_days: int = Field(7, ge=0, le=16)
    air: bool = True
    weather: bool = True
    name: str = Field("aqi_weather", max_length=80)


@router.post("/api/forecast/getdata")
def fc_getdata(req: FcData):
    from lulc_fetch import openmeteo
    pts = []
    for i, p in enumerate(req.points):
        try:
            lat, lon = float(p["lat"]), float(p["lon"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Every point needs lat and lon") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(400, f"Point {i + 1} is outside the Earth's coordinates")
        pts.append({"name": str(p.get("name") or f"point_{i + 1}")[:120], "lat": lat, "lon": lon})
    if not (req.air or req.weather):
        raise HTTPException(400, "Choose air quality, weather or both")

    def run(job):
        rows = openmeteo.history(pts, past_days=req.past_days, forecast_days=req.forecast_days, air=req.air, weather=req.weather)
        if not rows:
            raise ValueError("Open-Meteo returned no data for these points")
        core.TABLE_DIR.mkdir(exist_ok=True)
        out = core.unique(core.TABLE_DIR.path, f"{core.safe_stem(req.name, 'aqi_weather')}.csv")
        openmeteo.save_csv(rows, out)
        aqi = [r["aqi"] for r in rows if r.get("aqi") is not None]
        return {"path": ws.rel(out), "rows": len(rows), "places": len(pts), "start": rows[0]["time"], "end": max(r["time"] for r in rows),
                "last_aqi_time": max((r["time"] for r in rows if r.get("aqi") is not None), default=None),
                "aqi_range": [min(aqi), max(aqi)] if aqi else None, "columns": sorted({k for r in rows for k in r}),
                "distinct_air": len({(r.get("pm2_5"), r["time"]) for r in rows if r.get("pm2_5") is not None}) / max(1, len({r["time"] for r in rows if r.get("pm2_5") is not None}))}

    what = " & ".join(x for x, on in (("AQI", req.air), ("weather", req.weather)) if on)
    return core.jobs.submit("fcdata", f"Get {what} · {len(pts)} place{'s' if len(pts) > 1 else ''}", {"places": len(pts)}, run).to_dict()
