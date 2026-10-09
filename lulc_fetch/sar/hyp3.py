"""ASF HyP3: Sentinel-1 processing on demand, free with a NASA Earthdata login (a monthly allowance of credits).

    search(aoi, start, end, level)   Sentinel-1 scenes in ASF's catalogue (SLC for InSAR, GRD_HD for RTC); no login
    pairs(scenes, …)                 InSAR pairs: the same track and frame, each date with the next one(s)
    Client(token | user, password)   user(), request_access(use_case), costs(), submit(jobs), jobs(…), job(id)
    download(job, out_dir)           a finished job's zip, unpacked: its GeoTIFFs

Job types used here: INSAR_GAMMA (two SLC scenes → interferogram: unwrapped phase, coherence, amplitude, optionally
line-of-sight and vertical displacement) and RTC_GAMMA (one GRD scene → γ⁰ or σ⁰ terrain-corrected with GAMMA, the
same flattening as Planetary Computer's RTC). Results stay on ASF's servers about 14 days.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

import requests

API = "https://hyp3-api.asf.alaska.edu"
SEARCH = "https://api.daac.asf.alaska.edu/services/search/param"
URS = ("https://urs.earthdata.nasa.gov/oauth/authorize?response_type=code&client_id=BO_n7nTIlMljdvU6kRRB3g"
       "&redirect_uri=https://auth.asf.alaska.edu/login")   # ASF's Earthdata login (as hyp3_sdk does it)
ACCESS_HELP = "https://hyp3-docs.asf.alaska.edu/using/requesting_access/"
TOKEN_HELP = "https://urs.earthdata.nasa.gov/documentation/for_users/user_token"
JOB_TYPES = ("INSAR_GAMMA", "RTC_GAMMA")


# ------------------------------------------------------------------ finding scenes (no login)
def _wkt(aoi: dict) -> str:
    from shapely.geometry import shape
    g = shape(aoi)
    g = g.simplify(0.001) if len(g.wkt) > 4000 else g
    return g.wkt


def search(aoi: dict, start: str, end: str, *, level: str = "SLC", orbit: str | None = None, path: int | None = None,
           frame: int | None = None, limit: int = 250) -> list[dict]:
    """Sentinel-1 IW scenes over aoi in ASF's catalogue, sorted by date. level 'SLC' (for InSAR) or 'GRD_HD' (RTC)."""
    p = {"dataset": "SENTINEL-1", "processingLevel": level, "beamMode": "IW", "intersectsWith": _wkt(aoi), "start": start, "end": end,
         "output": "geojson", "maxResults": limit}
    if orbit:
        p["flightDirection"] = orbit.upper()
    if path:
        p["relativeOrbit"] = int(path)
    if frame:
        p["frame"] = int(frame)
    r = requests.get(SEARCH, params=p, timeout=90)
    r.raise_for_status()
    from shapely.geometry import shape
    a = shape(aoi)
    out = []
    for f in r.json().get("features", []):
        pr = f["properties"]
        cover = shape(f["geometry"]).intersection(a).area / a.area if f.get("geometry") and a.area else None
        out.append({"name": pr["sceneName"], "date": pr["startTime"][:10], "time": pr["startTime"][11:19], "platform": pr["sceneName"][:3],
                    "path": pr.get("pathNumber"), "frame": pr.get("frameNumber"), "orbit_direction": (pr.get("flightDirection") or "").lower(),
                    "polarisations": pr.get("polarization"), "level": pr.get("processingLevel"), "size_mb": round((pr.get("bytes") or 0) / 1e6),
                    "coverage": round(100 * cover, 1) if cover is not None else None, "absolute_orbit": pr.get("orbit")})
    return sorted(out, key=lambda s: (s["date"], s["time"]))


def pairs(scenes: list[dict], *, step: int = 1, max_days: int = 48) -> list[dict]:
    """InSAR pairs: scenes of the same track and frame, each date with the next `step` dates (step 1: 6 / 12-day
    pairs), at most max_days apart (coherence fades with time)."""
    groups: dict = {}
    for s in scenes:
        groups.setdefault((s["path"], s["frame"], s["orbit_direction"]), []).append(s)
    out = []
    for (pth, fr, od), g in groups.items():
        g = sorted(g, key=lambda s: s["date"])
        for i, ref in enumerate(g):
            for sec in g[i + 1: i + 1 + step]:
                days = (dt.date.fromisoformat(sec["date"]) - dt.date.fromisoformat(ref["date"])).days
                if 0 < days <= max_days:
                    out.append({"reference": ref["name"], "secondary": sec["name"], "dates": [ref["date"], sec["date"]], "days": days,
                                "path": pth, "frame": fr, "orbit_direction": od})
    return out


# ------------------------------------------------------------------ the HyP3 API (Earthdata login)
class AuthError(RuntimeError):
    pass


class Client:
    """A HyP3 session: an Earthdata Login bearer token (recommended: urs.earthdata.nasa.gov ▸ Generate Token), or the
    Earthdata username and password (ASF's login cookie)."""

    def __init__(self, token: str | None = None, username: str | None = None, password: str | None = None):
        self.s = requests.Session()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token.strip()}"
        elif username and password:
            r = self.s.get(URS, auth=(username, password), timeout=60)
            if r.status_code == 401 or "asf-urs" not in self.s.cookies:
                raise AuthError("Earthdata didn't accept the username / password (or ASF's apps aren't authorised in your Earthdata profile)")
        else:
            raise AuthError("Add your NASA Earthdata login (Credentials ▸ NASA Earthdata): a token, or username and password")

    def _call(self, method: str, path: str, **kw):
        r = self.s.request(method, API + path, timeout=120, **kw)
        if r.status_code == 401:
            raise AuthError("HyP3 didn't accept the Earthdata login: check it in Credentials ▸ NASA Earthdata (tokens expire after 60 days)")
        if r.status_code == 403:
            raise AuthError(r.json().get("detail") or "HyP3 access not approved yet")
        if not r.ok:
            try:
                d = r.json().get("detail") or r.text
            except ValueError:
                d = r.text
            raise RuntimeError(f"HyP3: {str(d)[:400]}")
        return r.json()

    def user(self) -> dict:
        return self._call("GET", "/user")

    def request_access(self, use_case: str, access_code: str | None = None) -> dict:
        body = {"use_case": use_case}
        if access_code:
            body["access_code"] = access_code
        return self._call("PATCH", "/user", json=body)

    def costs(self) -> dict:
        return self._call("GET", "/costs")

    def submit(self, jobs: list[dict], validate_only: bool = False) -> list[dict]:
        return self._call("POST", "/jobs", json={"jobs": jobs, "validate_only": validate_only})["jobs"]

    def jobs(self, name: str | None = None, days: int = 30) -> list[dict]:
        start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"start": start, **({"name": name} if name else {})}
        out, url = [], "/jobs"
        while url:
            d = self._call("GET", url, params=params if url == "/jobs" else None)
            out += d.get("jobs", [])
            nxt = d.get("next")
            url = nxt.replace(API, "") if nxt else None
            if len(out) > 1000:
                break
        return out

    def job(self, job_id: str) -> dict:
        return self._call("GET", f"/jobs/{job_id}")


def insar_job(ref: str, sec: str, *, name: str, looks: str = "20x4", displacement: bool = True, inc_map: bool = True,
              water_mask: bool = False, wrapped: bool = False, dem: bool = False) -> dict:
    return {"job_type": "INSAR_GAMMA", "name": name, "job_parameters": {
        "granules": [ref, sec], "looks": looks, "include_displacement_maps": displacement, "include_inc_map": inc_map,
        "apply_water_mask": water_mask, "include_wrapped_phase": wrapped, "include_dem": dem}}


def rtc_job(granule: str, *, name: str, resolution: int = 30, radiometry: str = "gamma0", speckle: bool = False,
            dem_matching: bool = False, inc_map: bool = True, scattering_area: bool = False, dem: bool = False) -> dict:
    return {"job_type": "RTC_GAMMA", "name": name, "job_parameters": {
        "granules": [granule], "resolution": float(resolution), "radiometry": radiometry, "scale": "power", "speckle_filter": speckle,
        "dem_matching": dem_matching, "include_inc_map": inc_map, "include_scattering_area": scattering_area, "include_dem": dem}}


def summary(job: dict) -> dict:
    """What the app shows of a job."""
    p = job.get("job_parameters") or {}
    return {"id": job["job_id"], "type": job["job_type"], "name": job.get("name"), "status": job.get("status_code"),
            "granules": p.get("granules", []), "submitted": job.get("request_time"), "expires": job.get("expiration_time"),
            "credits": job.get("credit_cost"), "files": [{"name": f.get("filename"), "size_mb": round((f.get("size") or 0) / 1e6, 1)} for f in job.get("files") or []],
            "processing_s": (job.get("processing_times") or [None])[-1] if isinstance(job.get("processing_times"), list) else job.get("processing_times")}


def download(job: dict, out_dir: Path, progress_cb=None) -> list[str]:
    """A finished job's product zip(s), unpacked into out_dir: the GeoTIFFs inside (and the README / metadata)."""
    if job.get("status_code") != "SUCCEEDED":
        raise RuntimeError(f"The job is {str(job.get('status_code', '?')).lower()}, not finished")
    out_dir.mkdir(parents=True, exist_ok=True)
    tifs = []
    for f in job.get("files") or []:
        z = out_dir / f["filename"]
        if not z.exists():
            with requests.get(f["url"], stream=True, timeout=600) as r:
                r.raise_for_status()
                total, got = int(r.headers.get("content-length") or f.get("size") or 0), 0
                part = z.with_suffix(".part")
                with open(part, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                        got += len(chunk)
                        if progress_cb and total:
                            progress_cb(got / total)
                part.replace(z)
        if zipfile.is_zipfile(z):
            with zipfile.ZipFile(z) as zz:
                for n in zz.namelist():
                    p = Path(n)
                    if p.is_absolute() or ".." in p.parts:   # only plain paths inside the folder
                        continue
                    zz.extract(n, out_dir)
                    if n.lower().endswith((".tif", ".tiff")):
                        tifs.append(str(out_dir / n))
            z.unlink()
        elif str(z).lower().endswith((".tif", ".tiff")):
            tifs.append(str(z))
    return sorted(tifs)
