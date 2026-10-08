"""Geometry of a GRD product: where each pixel of a map grid is in the radar image (Range-Doppler terrain correction).

For every output pixel: its position on the ground (latitude, longitude and ellipsoidal height from the DEM + geoid)
→ the zero-Doppler time when the satellite saw it (Newton iterations on the interpolated orbit) → its azimuth line,
and its slant range → ground range (the product's SRGR polynomials) → its range pixel. The radar image is then sampled
there. The same geometry gives the incidence angles (ellipsoid and local), layover and shadow, and terrain flattening
(the angular volume model of Vollrath et al. 2020, as in GEE's slope correction).

    Orbit(state vectors)              position / velocity / acceleration at any time
    precise_orbit(mission, t0, t1)    ESA's precise orbit (POEORB) state vectors, when published (~20 days after)
    geoid(lon, lat)                   EGM96 undulation (m): ellipsoidal height = DEM height + geoid
    dem_for(grid)                     Copernicus DEM GLO-30 (Planetary Computer) or a DEM file, on a grid
    RangeDoppler(geometry, orbit)     .locate(lat, lon, h) → line, pixel, satellite position
"""

from __future__ import annotations

import io
import logging
import math
import re
import zipfile
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
A, F = 6378137.0, 1 / 298.257223563
E2 = F * (2 - F)
C = 299792458.0
GEOID_URL = "https://cdn.proj.org/us_nga_egm96_15.tif"
ORBIT_URL = "https://step.esa.int/auxdata/orbits/Sentinel-1/POEORB/{sat}/{y}/{m:02d}/"


def ecef(lat, lon, h):
    la, lo = np.radians(lat), np.radians(lon)
    n = A / np.sqrt(1 - E2 * np.sin(la) ** 2)
    return np.stack([(n + h) * np.cos(la) * np.cos(lo), (n + h) * np.cos(la) * np.sin(lo), (n * (1 - E2) + h) * np.sin(la)], -1)


def enu_basis(lat, lon):
    """East, north, up unit vectors (ECEF) at each point: arrays [..., 3]."""
    la, lo = np.radians(lat), np.radians(lon)
    e = np.stack([-np.sin(lo), np.cos(lo), np.zeros_like(lo)], -1)
    n = np.stack([-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)], -1)
    u = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)
    return e, n, u


class Orbit:
    """State vectors (t, x, y, z, vx, vy, vz) → a degree-≤8 polynomial per axis over the scene (as S1TBX's Lagrange)."""

    def __init__(self, sv: np.ndarray, t0: float, t1: float, source: str = "annotation"):
        sv = sv[np.argsort(sv[:, 0])]
        keep = (sv[:, 0] >= t0 - 60) & (sv[:, 0] <= t1 + 60)
        if keep.sum() < 9:   # at least nine vectors around the scene
            mid = np.argmin(np.abs(sv[:, 0] - (t0 + t1) / 2))
            keep = np.zeros(len(sv), bool)
            keep[max(0, mid - 5):mid + 6] = True
        sv = sv[keep]
        if len(sv) < 4:
            raise ValueError("Too few orbit state vectors to interpolate the orbit")
        self.tm, self.source = sv[:, 0].mean(), source
        tt = sv[:, 0] - self.tm
        deg = min(8, len(sv) - 1)
        self.p = [np.polynomial.Polynomial.fit(tt, sv[:, k], deg) for k in (1, 2, 3)]
        self.v = [p.deriv() for p in self.p]
        self.a = [p.deriv(2) for p in self.p]
        self.residual_m = float(np.sqrt(np.mean([(p(tt) - sv[:, k]) ** 2 for p, k in zip(self.p, (1, 2, 3))])))   # fit check

    def at(self, t: np.ndarray):
        tt = np.asarray(t) - self.tm
        return (np.stack([p(tt) for p in self.p], -1), np.stack([v(tt) for v in self.v], -1), np.stack([a(tt) for a in self.a], -1))


def precise_orbit(mission: str, t0: float, t1: float, cache: Path | None = None) -> np.ndarray | None:
    """ESA's precise orbit (POEORB) state vectors covering [t0, t1] (epoch seconds), or None when not (yet) published
    or not reachable. Files are listed by the month they were made in (~20 days after): that month and the next."""
    import datetime as dt

    import requests
    day = dt.datetime.fromtimestamp(t0, dt.timezone.utc)
    fmt = "%Y%m%dT%H%M%S"
    for k in (0, 1):
        m, y = (day.month - 1 + k) % 12 + 1, day.year + (day.month - 1 + k) // 12
        folder = ORBIT_URL.format(sat=mission, y=y, m=m)
        try:
            r = requests.get(folder, timeout=30)
        except requests.RequestException:
            return None
        if not r.ok:
            continue
        for name in sorted(set(re.findall(r'href="([^"/]*POEORB[^"]*\.EOF\.zip)"', r.text))):
            a, b = (dt.datetime.strptime(x, fmt).replace(tzinfo=dt.timezone.utc).timestamp() for x in re.search(r"_V(\d{8}T\d{6})_(\d{8}T\d{6})", name).groups())
            if not (a <= t0 - 60 and b >= t1 + 60):
                continue
            f = cache / name if cache else None
            if f and f.is_file():
                data = f.read_bytes()
            else:
                rr = requests.get(folder + name, timeout=180)
                if not rr.ok:
                    return None
                data = rr.content
                if f:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(data)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read(next(n for n in z.namelist() if n.endswith(".EOF"))).decode()
            rows = []
            for osv in re.findall(r"<OSV>(.*?)</OSV>", xml, re.S):
                tt = dt.datetime.fromisoformat(re.search(r"<UTC>UTC=([^<]+)</UTC>", osv).group(1)).replace(tzinfo=dt.timezone.utc).timestamp()
                if t0 - 120 <= tt <= t1 + 120:
                    rows.append([tt] + [float(re.search(rf"<{k}[^>]*>([^<]+)</{k}>", osv).group(1)) for k in ("X", "Y", "Z", "VX", "VY", "VZ")])
            return np.array(rows) if len(rows) >= 9 else None
    return None


_GEOID = {}


def geoid(lon: np.ndarray, lat: np.ndarray, cache: Path | None = None) -> np.ndarray:
    """EGM96 geoid undulation N (m) at each point (PROJ's grid, 2.7 MB, downloaded once); 0 when it can't be had."""
    if "grid" not in _GEOID:
        import rasterio
        f = (cache / "us_nga_egm96_15.tif") if cache else None
        try:
            if f and not f.is_file():
                import requests
                r = requests.get(GEOID_URL, timeout=120)
                r.raise_for_status()
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(r.content)
            with rasterio.open(f if f else "/vsicurl/" + GEOID_URL) as s:
                _GEOID["grid"] = (s.read(1).astype("float64"), s.transform)
        except Exception as e:   # noqa: BLE001 (no internet: heights stay geoid-based, noted in the result)
            log.warning("Geoid not available (%s): heights are used as they are", e)
            _GEOID["grid"] = None
    g = _GEOID["grid"]
    if g is None:
        return np.zeros_like(np.asarray(lat, float))
    from scipy.ndimage import map_coordinates
    arr, tr = g
    col = (np.asarray(lon) % 360 - tr.c) / tr.a
    col = np.where(col < 0, col + arr.shape[1], col)
    row = (np.asarray(lat) - tr.f) / tr.e
    return map_coordinates(arr, [row.ravel(), col.ravel()], order=1, mode="wrap").reshape(np.shape(lat))


def dem_for(crs, transform, shape, dem_path: str | None = None, cache: Path | None = None) -> tuple[np.ndarray, str]:
    """Heights (m above the geoid) on a grid: from a DEM file, or Copernicus DEM GLO-30 tiles from Planetary Computer."""
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    out = np.full(shape, np.nan, "float64")
    if dem_path:
        with rasterio.open(dem_path) as s:
            reproject(rasterio.band(s, 1), out, dst_transform=transform, dst_crs=crs, dst_nodata=np.nan, resampling=Resampling.bilinear)
        return out, Path(dem_path).name
    import planetary_computer as pc
    import pystac_client
    from rasterio.transform import array_bounds
    w, s_, e, n = transform_bounds(crs, "EPSG:4326", *array_bounds(*shape, transform))
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    items = list(cat.search(collections=["cop-dem-glo-30"], bbox=[w - 0.02, s_ - 0.02, e + 0.02, n + 0.02]).items())
    if not items:
        raise ValueError("No Copernicus DEM tiles for this area")
    for it in items:
        tmp = np.full(shape, np.nan, "float64")
        with rasterio.open("/vsicurl/" + it.assets["data"].href) as s:
            reproject(rasterio.band(s, 1), tmp, dst_transform=transform, dst_crs=crs, dst_nodata=np.nan, resampling=Resampling.bilinear)
        out = np.where(np.isnan(out), tmp, out)
    return out, f"Copernicus DEM GLO-30 ({len(items)} tile{'s' if len(items) > 1 else ''}, Planetary Computer)"


class RangeDoppler:
    """Map positions → radar image positions for a GRD product (zero-Doppler, SRGR ground range)."""

    def __init__(self, g, orbit: Orbit):
        self.g, self.orbit = g, orbit
        self.tc = np.array([c[0] for c in g.srgr])
        self.t_last = g.first_line + (g.lines - 1) * g.line_interval

    def zero_doppler(self, P: np.ndarray, iters: int = 8) -> np.ndarray:
        t = np.full(P.shape[:-1], (self.g.first_line + self.t_last) / 2)
        for _ in range(iters):
            S, V, Acc = self.orbit.at(t)
            d = P - S
            f = (d * V).sum(-1)
            df = -(V * V).sum(-1) + (d * Acc).sum(-1)
            step = f / df
            t = t - step
            if np.nanmax(np.abs(step)) < 1e-7:
                break
        return t

    def ground_range(self, t: np.ndarray, R: np.ndarray) -> np.ndarray:
        """Slant range R at azimuth time t → ground range (m), interpolating between the SRGR records in time."""
        tc = self.tc
        i = np.clip(np.searchsorted(tc, t) - 1, 0, max(0, len(tc) - 2))
        out = np.zeros_like(R)
        for k in np.unique(i):
            sel = i == k
            vals = []
            for j in (k, min(k + 1, len(tc) - 1)):
                _, sr0, co = self.g.srgr[j]
                vals.append(np.polynomial.polynomial.polyval(R[sel] - sr0, co))
            w = np.clip((t[sel] - tc[k]) / max(tc[min(k + 1, len(tc) - 1)] - tc[k], 1e-9), 0, 1) if len(tc) > 1 else 0
            out[sel] = vals[0] * (1 - w) + vals[1] * w
        return out

    def locate(self, lat, lon, h):
        """(line, pixel, satellite position [..., 3], target position [..., 3]) for points (degrees, ellipsoidal metres)."""
        P = ecef(lat, lon, h)
        t = self.zero_doppler(P)
        S, _, _ = self.orbit.at(t)
        R = np.linalg.norm(P - S, axis=-1)
        line = (t - self.g.first_line) / self.g.line_interval
        pixel = self.ground_range(t, R) / self.g.range_spacing
        return line, pixel, S, P


def slopes(dem: np.ndarray, res_xy: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """East and north slopes (dz/dx, dz/dy) of a DEM on a north-up grid (rows run north → south)."""
    dx, dy = res_xy
    gy, gx = np.gradient(np.where(np.isfinite(dem), dem, np.nanmean(dem)))
    return gx / dx, -gy / dy


def angles(lat, lon, S, P, slope: tuple | None = None):
    """Ellipsoid incidence θ, local incidence θ_loc (degrees), and the range-direction slope α_r (degrees, positive when
    the slope faces the sensor), from the look vector and the terrain slopes (dz/dx, dz/dy east and north)."""
    los = S - P
    los /= np.linalg.norm(los, axis=-1, keepdims=True)
    e, n, u = enu_basis(lat, lon)
    lz = (los * u).sum(-1)
    theta = np.degrees(np.arccos(np.clip(lz, -1, 1)))
    if slope is None:
        return theta, theta, np.zeros_like(theta)
    dzdx, dzdn = slope
    le, ln = (los * e).sum(-1), (los * n).sum(-1)
    hz = np.hypot(le, ln)
    rise_to_sensor = (dzdx * le + dzdn * ln) / np.where(hz > 0, hz, 1)   # dz per metre walking towards the sensor
    alpha_r = np.degrees(np.arctan(-rise_to_sensor))   # a slope facing the sensor goes down towards it: α_r > 0
    nt = np.stack([-dzdx, -dzdn, np.ones_like(dzdx)], -1)
    nt /= np.linalg.norm(nt, axis=-1, keepdims=True)
    theta_loc = np.degrees(np.arccos(np.clip(nt[..., 0] * le + nt[..., 1] * ln + nt[..., 2] * lz, -1, 1)))
    return theta, theta_loc, alpha_r


def flatten_factor(theta: np.ndarray, alpha_r: np.ndarray) -> np.ndarray:
    """Vollrath et al. (2020) volume model: γ⁰_flat = γ⁰ / factor, factor = tan(90° − θ + α_r) / tan(90° − θ) (above 1 on
    slopes facing the sensor, which look too bright before flattening)."""
    t, a = np.radians(theta), np.radians(alpha_r)
    with np.errstate(all="ignore"):
        return np.tan(math.pi / 2 - t + a) / np.tan(math.pi / 2 - t)


def layover_shadow(theta: np.ndarray, alpha_r: np.ndarray) -> np.ndarray:
    """0 fine, 1 layover (a slope facing the sensor steeper than the incidence angle), 2 shadow (a slope facing away
    steeper than 90° − incidence)."""
    m = np.zeros(theta.shape, "uint8")
    m[alpha_r > theta] = 1
    m[alpha_r < -(90 - theta)] = 2
    return m
