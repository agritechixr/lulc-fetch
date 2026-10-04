"""Data library: GIS data kept as dataset repositories on Hugging Face (free, public), fetched by the app when needed.

Every public dataset of the library's account(s) is listed; any file in them can be added to the map: vectors (GeoJSON,
shapefile, KML / KMZ, zipped shapefile), rasters (GeoTIFF) and tables (CSV / Excel / Parquet). A ``catalog.json`` at the
top of a dataset (optional, see ``build_catalog``) gives friendlier titles, categories, feature counts and extents.

    python -m lulc_fetch.library catalog data/hf_datasets/indian-shapefiles      # write catalog.json for a folder
    python -m lulc_fetch.library upload  data/hf_datasets/indian-shapefiles ixrbhii/indian-shapefiles   # needs `hf auth login`

Downloads need no account. Files are checked against Hugging Face's SHA-256 (large files) and kept, so each is fetched once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path

from . import progress

log = logging.getLogger(__name__)
HF = "https://huggingface.co"
ACCOUNTS = ("ixrbhii",)   # whose public datasets the library lists (more can be added in the app's settings)
RASTER_EXTS = {".tif", ".tiff"}
VECTOR_EXTS = {".geojson", ".shp", ".kml", ".kmz", ".gpkg", ".zip"}
TABLE_EXTS = {".csv", ".tsv", ".xlsx", ".parquet"}
SHP_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def _get(url: str, **kw):
    import requests
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=kw.pop("timeout", 30), headers={"User-Agent": "LULC-Fetch"}, **kw)
            if r.status_code >= 500 and attempt < 3:
                raise requests.ConnectionError(f"server error {r.status_code}")
            return r
        except requests.RequestException as e:
            if attempt == 3:
                raise RuntimeError(f"Couldn't reach Hugging Face: {e}") from e
            time.sleep(1.5 * (attempt + 1))


def kind_of(path: str) -> str:
    """raster | vector | table | other. A plain .json is "other" (it may be any data): the catalog marks GeoJSON ones."""
    ext = Path(path).suffix.lower()
    if ext in RASTER_EXTS:
        return "raster"
    if ext in VECTOR_EXTS:
        return "vector"
    if ext in TABLE_EXTS:
        return "table"
    return "other"


def _summary(text: str, title: str = "") -> str:
    """The first sentences of a dataset card's text (Hugging Face flattens the card: tables and headings run together)."""
    s = text.split("|")[0].strip()
    if title and s.lower().startswith(title.lower()):
        s = s[len(title):].strip()
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", s):
        if len(out) + len(sent) > 260:
            break
        out += (" " if out else "") + sent
    return out or s[:260]


def datasets(accounts=ACCOUNTS) -> list[dict]:
    """The public datasets of these accounts, newest first."""
    out = []
    for a in accounts:
        r = _get(f"{HF}/api/datasets", params={"author": a, "full": "true", "limit": 200})
        if r.status_code != 200:
            raise RuntimeError(f"Hugging Face answered {r.status_code} for the datasets of {a}")
        for d in r.json():
            if d.get("private") or d.get("disabled"):
                continue
            card = d.get("cardData") or {}
            out.append({"repo": d["id"], "title": card.get("pretty_name") or d["id"].split("/")[-1].replace("-", " ").replace("_", " "),
                        "description": _summary(d.get("description") or "", card.get("pretty_name") or ""), "license": card.get("license"),
                        "modified": d.get("lastModified"), "url": f"{HF}/datasets/{d['id']}"})
    return sorted(out, key=lambda d: d.get("modified") or "", reverse=True)


def files(repo: str) -> dict:
    """Every file of a dataset (path, size, kind, sha256 for large files), enriched from its catalog.json if there is one."""
    url, out = f"{HF}/api/datasets/{repo}/tree/main", []
    params = {"recursive": "true", "expand": "false"}
    while url:
        r = _get(url, params=params)
        if r.status_code == 404:
            raise RuntimeError(f"No dataset {repo} on Hugging Face")
        r.raise_for_status()
        for f in r.json():
            if f.get("type") == "file":
                out.append({"path": f["path"], "size": f.get("size", 0), "kind": kind_of(f["path"]),
                            "sha256": (f.get("lfs") or {}).get("oid")})
        m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
        url, params = (m.group(1), None) if m else (None, None)
    cat = {}
    if any(f["path"] == "catalog.json" for f in out):
        r = _get(f"{HF}/datasets/{repo}/resolve/main/catalog.json")
        if r.status_code == 200:
            cat = {e["path"]: e for e in r.json().get("files", [])}
    for f in out:
        f.update({k: v for k, v in cat.get(f["path"], {}).items() if k not in ("path", "size")})
    # a shapefile is one entry: its .shx / .dbf / .prj / .cpg come along
    shp = {f["path"][:-4] for f in out if f["path"].lower().endswith(".shp")}
    out = [f for f in out if not (Path(f["path"]).suffix.lower() in SHP_PARTS[1:] and f["path"][:-4] in shp)]
    return {"repo": repo, "files": sorted(out, key=lambda f: f["path"].lower()), "catalog": bool(cat)}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(repo: str, path: str, dest_root: Path, sha256: str | None = None, size: int | None = None) -> Path:
    """Download one file of a dataset into dest_root/<repo>/<path> (once; a shapefile with its parts), checked against
    its SHA-256 when Hugging Face gives one. Returns the local file."""
    import requests
    dest = Path(dest_root) / repo.replace("/", "__") / path
    parts = [path] + ([path[:-4] + e for e in SHP_PARTS[1:]] if path.lower().endswith(".shp") else [])
    for k, rel in enumerate(parts):
        out = Path(dest_root) / repo.replace("/", "__") / rel
        if out.is_file() and (k or size is None or out.stat().st_size == size):
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        url = f"{HF}/datasets/{repo}/resolve/main/{requests.utils.quote(rel)}"
        with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "LULC-Fetch"}) as r:
            if r.status_code == 404 and k:
                continue   # optional shapefile part (.cpg / .prj) not there
            if r.status_code != 200:
                raise RuntimeError(f"Hugging Face answered {r.status_code} for {rel}")
            total, done = int(r.headers.get("Content-Length") or size or 0), 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    if total and not k:
                        progress.update(min(0.97, done / total), f"Downloading {Path(rel).name}: {done / 1e6:.0f} of {total / 1e6:.0f} MB")
        if k == 0 and sha256 and _sha256(tmp) != sha256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"The download of {Path(rel).name} was damaged (checksum differs): try again")
        tmp.replace(out)
    return dest


# ------------------------------------------------------------------ preparing a folder for upload
_KINDS = [  # file name pattern → what it is
    (r"village", "Villages"), (r"sub.?district.*hq|subdistrict.*hq|sub district hq", "Sub-district HQs"), (r"district.*hq", "District HQs"),
    (r"sub.?districts?", "Sub-districts"), (r"districts?", "Districts"), (r"assembly", "Assembly constituencies"),
    (r"parliament|parlimentary", "Parliamentary constituencies"), (r"pincode", "PIN-code areas"), (r"railway", "Railways"),
    (r"highway", "National highways"), (r"energy", "Energy plants"), (r"police", "Police stations"), (r"states", "States"),
    (r"_state|^state", "State boundary"),
]


def _kind_title(name: str) -> str:
    n = name.lower().replace("_", " ")
    return next((t for p, t in _KINDS if re.search(p.replace("_", " "), n)), "")


def build_catalog(folder: str | Path, title: str = "", source: str = "") -> dict:
    """catalog.json for a folder of GIS files: title, group, place, kind, size, features, geometry types and extent of
    each. Read by the app's Library (optional: without it the file names are shown)."""
    folder = Path(folder)
    entries = []
    def geojson(p):   # a .json that holds GeoJSON
        with open(p, "rb") as fh:
            head = fh.read(2048).decode("utf-8", "ignore")
        return '"FeatureCollection"' in head or '"features"' in head

    paths = sorted(p for p in folder.rglob("*") if p.is_file() and p.name != "catalog.json" and not p.name.startswith(".")
                   and (kind_of(str(p)) != "other" or p.suffix.lower() == ".json" and geojson(p)))
    for n, p in enumerate(paths, 1):
        rel = p.relative_to(folder).as_posix()
        parts = rel.split("/")
        e = {"path": rel, "size": p.stat().st_size, "kind": "vector" if p.suffix.lower() == ".json" else kind_of(rel), "group": parts[0].title() if len(parts) > 1 else "",
             "place": parts[1].title() if len(parts) > 2 else "", "title": p.stem.replace("_", " ").title(), "type": _kind_title(p.stem)}
        if not e["type"] and "metropolitan" in rel.lower():
            e["type"] = "City wards"
        if e["kind"] == "vector" and p.suffix.lower() in (".geojson", ".json"):
            try:
                gj = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                feats = gj.get("features") or []
                types, xs, ys = set(), [], []

                def walk(c):
                    if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
                        xs.append(c[0]); ys.append(c[1])
                    elif isinstance(c, (list, tuple)):
                        for x in c:
                            walk(x)
                for f in feats:
                    g = f.get("geometry") or {}
                    if g.get("type"):
                        types.add(g["type"].replace("Multi", ""))
                    walk(g.get("coordinates"))
                e.update(features=len(feats), geometry=sorted(types))
                if xs:
                    e["bbox"] = [round(min(xs), 5), round(min(ys), 5), round(max(xs), 5), round(max(ys), 5)]
                fields = list((feats[0].get("properties") or {}).keys())[:20] if feats else []
                e["fields"] = fields
            except (ValueError, UnicodeDecodeError, MemoryError) as err:
                e["note"] = f"not read: {type(err).__name__}"
        entries.append(e)
        progress.update(n / len(paths), f"{n} / {len(paths)}: {rel}")
        log.info("%d / %d  %s", n, len(paths), rel)
    cat = {"title": title or folder.name.replace("-", " ").title(), "source": source, "files": entries,
           "made": time.strftime("%Y-%m-%d")}
    (folder / "catalog.json").write_text(json.dumps(cat, indent=1, ensure_ascii=False), encoding="utf-8")
    return cat


def upload(folder: str | Path, repo: str, private: bool = False) -> str:
    """Upload a folder as a Hugging Face dataset (needs `hf auth login` once). One commit per sub-folder (e.g. each
    state), skipping files already there with the same size, so a broken upload continues where it stopped."""
    from huggingface_hub import HfApi
    folder = Path(folder)
    api = HfApi()
    api.create_repo(repo, repo_type="dataset", private=private, exist_ok=True)
    have = {f.path: getattr(f, "size", None) for f in api.list_repo_tree(repo, repo_type="dataset", recursive=True) if hasattr(f, "size")}
    local = [p for p in sorted(folder.rglob("*")) if p.is_file() and not p.name.startswith(".")]
    todo = [p for p in local if have.get(p.relative_to(folder).as_posix()) != p.stat().st_size]
    groups: dict[str, list[Path]] = {}
    for p in todo:   # top-level files together; then by folder two levels deep (STATES/KARNATAKA, INDIA, …)
        parts = p.relative_to(folder).parts
        groups.setdefault("/".join(parts[:2]) if len(parts) > 2 else parts[0] if len(parts) > 1 else "", []).append(p)
    log.info("%d of %d files to upload, in %d commits", len(todo), len(local), len(groups))
    for n, (g, ps) in enumerate(sorted(groups.items()), 1):
        mb = sum(p.stat().st_size for p in ps) / 1e6
        log.info("%d / %d  %s: %d files, %.0f MB", n, len(groups), g or "(top level)", len(ps), mb)
        api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(folder),
                          allow_patterns=[p.relative_to(folder).as_posix() for p in ps],
                          commit_message=f"Add {g or 'dataset card, licence and catalog'}")
    return f"{HF}/datasets/{repo}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd, *args = sys.argv[1:] or ["help"]
    if cmd == "catalog" and args:
        c = build_catalog(args[0], *(args[1:3]))
        print(f"catalog.json: {len(c['files'])} files")
    elif cmd == "upload" and len(args) == 2:
        print(upload(*args))
    else:
        print(__doc__)
