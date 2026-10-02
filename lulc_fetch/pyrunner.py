"""Run a user's Python script on a table, in its own process (so it can be stopped and can't hang the app).

    python -m lulc_fetch.pyrunner in.parquet script.py out.parquet meta.json

The script sees:
  df            the table (pandas DataFrame); change it or assign a new one to df
  pd, np, math, re, datetime
For vector layers (a __geometry__ column of GeoJSON) also:
  df["geometry"]                 shapely geometries in WGS 84 (read-only: it is dropped from the result)
  area_m2(g), area_ha(g), perimeter_m(g), length_m(g), centroid_xy(g)   measured in the local UTM zone
print(...) output is shown to the user. The row index identifies the original rows / features.
"""

from __future__ import annotations

import json
import sys


def _utm(g):
    from rasterio.warp import transform_geom
    from shapely.geometry import mapping, shape
    c = g.centroid
    epsg = (32600 if c.y >= 0 else 32700) + min(max(int((c.x + 180) // 6) + 1, 1), 60)
    return shape(transform_geom("EPSG:4326", f"EPSG:{epsg}", mapping(g)))


def area_m2(g):
    return float(_utm(g).area) if g is not None and g.geom_type.endswith("Polygon") else 0.0


def area_ha(g):
    return area_m2(g) / 1e4


def perimeter_m(g):
    return float(_utm(g).boundary.length) if g is not None and g.geom_type.endswith("Polygon") else 0.0


def length_m(g):
    return float(_utm(g).length) if g is not None else 0.0


def centroid_xy(g):
    c = g.centroid
    return (c.x, c.y)


def main(in_path, script_path, out_path, meta_path):
    import datetime
    import math
    import re

    import numpy as np
    import pandas as pd

    df = pd.read_parquet(in_path)
    df.index = df.pop("__row__") if "__row__" in df.columns else df.index
    has_geom = "__geometry__" in df.columns
    if has_geom:
        from shapely.geometry import shape
        df["geometry"] = [shape(json.loads(g)) if g else None for g in df.pop("__geometry__")]
    before = {"rows": len(df), "columns": [c for c in df.columns if c != "geometry"]}
    env = {"df": df, "pd": pd, "np": np, "math": math, "re": re, "datetime": datetime, "__name__": "__script__"}
    if has_geom:
        env.update(area_m2=area_m2, area_ha=area_ha, perimeter_m=perimeter_m, length_m=length_m, centroid_xy=centroid_xy)
    code = open(script_path, encoding="utf-8").read()
    exec(compile(code, "<your script>", "exec"), env)
    out = env.get("df")
    if isinstance(out, pd.Series):
        out = out.to_frame()
    if not isinstance(out, pd.DataFrame):
        raise TypeError("After the script, df must still be a table (a pandas DataFrame)")
    out = out.drop(columns=["geometry"], errors="ignore")
    out.columns = [str(c) for c in out.columns]
    if len(set(out.columns)) != len(out.columns):
        raise ValueError("The result has duplicate column names")
    for c in out.columns:   # mixed Python objects → text, so the table can be saved
        if out[c].dtype == object:
            vals = out[c].dropna()
            if len(vals) and not all(isinstance(v, str) for v in vals):
                if all(isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) for v in vals):
                    out[c] = pd.to_numeric(out[c])
                else:
                    out[c] = out[c].map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v))
    idx = out.index
    keep = pd.Series(idx, dtype="float64") if pd.api.types.is_numeric_dtype(idx) else pd.Series([float("nan")] * len(idx))
    out = out.reset_index(drop=True)
    out.insert(0, "__row__", keep.values)
    out.to_parquet(out_path, index=False)
    after = [c for c in out.columns if c != "__row__"]
    json.dump({"before": before, "after": {"rows": len(out), "columns": after},
               "added": [c for c in after if c not in before["columns"]],
               "removed": [c for c in before["columns"] if c not in after]}, open(meta_path, "w"))


if __name__ == "__main__":
    try:
        main(*sys.argv[1:5])
    except SystemExit:
        raise
    except BaseException:
        import traceback
        tb = traceback.format_exc().splitlines()
        # only the user's script lines and the error itself (not the runner's or pandas' internals)
        lines = []
        for i, ln in enumerate(tb):
            if ln.startswith('  File "<your script>"'):
                lines.append(ln.strip().replace('File "<your script>", ', ""))
        lines.append(tb[-1])
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)
