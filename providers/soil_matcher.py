"""
SoilGrids → WOFOST Soil File Matcher
=====================================
Fetches soil data (sand/silt/clay) from the SoilGrids API for given coordinates,
determines the texture class, and matches against WOFOST files in the soilType directory.

Usage:
    python soil_matcher.py

Output:
    - Summary table to console
    - soil_match_results.json
"""

import os
import re
import json
import time
import requests

try:
    import requests_cache
    cache_session = requests_cache.CachedSession(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".soilgrids_cache"),
        expire_after=-1,
    )
    USE_CACHE = True
except ImportError:
    cache_session = requests.Session()
    USE_CACHE = False

# Optional OpenLandMap helper (STAC + COG sampling) as fallback
try:
    # prefer package import when available
    import providers.openlandmap as openlandmap
except Exception:
    try:
        # fallback to direct module import when running as script
        import openlandmap
    except Exception:
        openlandmap = None

# ─── Configuration ────────────────────────────────────────────────────────────

SOILGRIDS_URL  = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SOIL_FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soils")
OUTPUT_JSON    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soil_match_results.json")

# SoilGrids fair-use: ~5 requests/minute → wait 13s on cache miss
API_SLEEP_SECONDS = 13


# ══════════════════════════════════════════════════════════════════════════════
# 1. WOFOST file reading
# ══════════════════════════════════════════════════════════════════════════════

def parse_wofost_soil_file(filepath: str) -> dict:
    """
    Reads a WOFOST file in .new / .awc / .sol format.
    Returns: dict — SOLNAM, SMW, SMFCF, SM0, CRAIRC, K0, SOPE, KSUB, AWC
    """
    result = {
        "filepath": filepath,
        "filename": os.path.basename(filepath),
    }

    with open(filepath, encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    # Strip comment lines and ** headers
    clean_lines = []
    for line in content.splitlines():
        if "!" in line:
            line = line[: line.index("!")]
        line = line.strip()
        if line and not line.startswith("**"):
            clean_lines.append(line)
    clean = "\n".join(clean_lines)

    # Scalar parameters
    for key in ("SMW", "SMFCF", "SM0", "CRAIRC", "K0", "SOPE", "KSUB",
                "SPADS", "SPODS", "SPASS", "SPOSS", "DEFLIM"):
        m = re.search(rf"\b{key}\s*=\s*([-\d.]+)", clean)
        if m:
            result[key] = float(m.group(1))

    # SOLNAM (from original content, including quotes)
    m = re.search(r"SOLNAM\s*=\s*'([^']+)'", content)
    result["SOLNAM"] = m.group(1).strip() if m else result["filename"]

    # AWC = field capacity - wilting point
    smw   = result.get("SMW")
    smfcf = result.get("SMFCF")
    if smw is not None and smfcf is not None:
        result["AWC"] = round(smfcf - smw, 4)

    return result


def load_soil_files(directory: str) -> list[dict]:
    """Loads all .new / .awc / .sol files from the soilType directory."""
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"soilType directory not found: {directory}")

    soils = []
    for fname in sorted(os.listdir(directory)):
        if fname.lower().endswith((".new", ".awc", ".sol")):
            path = os.path.join(directory, fname)
            try:
                soils.append(parse_wofost_soil_file(path))
            except Exception as exc:
                print(f"  [WARNING] {fname} could not be read: {exc}")

    if not soils:
        raise RuntimeError(f"No files found in soilType directory: {directory}")

    return soils


# ══════════════════════════════════════════════════════════════════════════════
# 2. SoilGrids API
# ══════════════════════════════════════════════════════════════════════════════

OPENLANDMAP_URL = "https://api.openlandmap.org/query/point"


def fetch_soilgrids(lat: float, lon: float, max_attempts: int = 4) -> dict | None:
    """Try SoilGrids v2.0 first, then OpenLandMap REST, finally STAC/COG fallback.

    Returns either a SoilGrids v2.0 JSON (with 'properties') or an OpenLandMap
    response object (with 'result') or None.
    """
    # 1) SoilGrids v2.0
    params = [
        ("lat", float(lat)),
        ("lon", float(lon)),
        ("property", "sand"),
        ("property", "silt"),
        ("property", "clay"),
        ("depth", "0-5cm"),
        ("value", "mean"),
    ]
    for attempt in range(1, max_attempts + 1):
        try:
            r = cache_session.get(SOILGRIDS_URL, params=params, headers={"Accept": "application/json"}, timeout=20)
            if r.status_code == 429:
                wait = 15 * attempt
                print(f"    [SoilGrids] Rate limit — waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                wait = 5 * attempt
                print(f"    [SoilGrids] Server error {r.status_code} — waiting {wait}s...")
                if attempt < max_attempts:
                    time.sleep(wait)
                    continue
                break
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            wait = 8 * attempt
            print(f"    [SoilGrids attempt {attempt}] Timeout — waiting {wait}s...")
            if attempt < max_attempts:
                time.sleep(wait)
        except Exception as exc:
            print(f"    [SoilGrids attempt {attempt}] {type(exc).__name__}: {exc}")
            if attempt < max_attempts:
                time.sleep(5)

    # 2) OpenLandMap REST as fallback
    layers = {
        "sand": "sol_sand.wfraction_usda.3a1a1a_m_250m_b0..0cm_1950..2017_v0.2",
        "silt": "sol_silt.wfraction_usda.3a1a1a_m_250m_b0..0cm_1950..2017_v0.2",
        "clay": "sol_clay.wfraction_usda.3a1a1a_m_250m_b0..0cm_1950..2017_v0.2",
    }
    params2 = {"lon": lon, "lat": lat, "coll": ",".join(layers.values())}
    for attempt in range(1, max_attempts + 1):
        try:
            resp = cache_session.get(OPENLANDMAP_URL, params=params2, headers={"Accept": "application/json"}, timeout=20)
            if resp.status_code == 429:
                wait = 15 * attempt
                print(f"    [OpenLandMap] Rate limit — waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 5 * attempt
                print(f"    [OpenLandMap] Server error {resp.status_code} — waiting {wait}s...")
                if attempt < max_attempts:
                    time.sleep(wait)
                    continue
                break
            if resp.status_code == 422:
                # bad request from API — skip to STAC fallback
                print(f"    [OpenLandMap] Bad request (422), skipping to STAC fallback")
                break
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout:
            wait = 8 * attempt
            print(f"    [OpenLandMap attempt {attempt}] Timeout — waiting {wait}s...")
            if attempt < max_attempts:
                time.sleep(wait)
        except Exception as exc:
            print(f"    [OpenLandMap attempt {attempt}] {type(exc).__name__}: {exc}")
            if attempt < max_attempts:
                time.sleep(5)

    # 3) STAC/COG fallback will be attempted by caller via fetch_openlandmap_texture
    return None


def extract_mean(resp, var_name: str) -> float | None:
    """
    Supports two response formats:
      - SoilGrids v2.0 JSON: has `properties.layers[].name` and depths[].values.mean
      - OpenLandMap REST JSON: has `result` list with keys containing var_name
    Returned value uses the OpenLandMap/SoilGrids numeric convention when possible
    (g/kg * 10) so 40% -> 400.
    """
    if resp is None:
        return None
    try:
        data = resp.json() if hasattr(resp, "json") else resp
        # SoilGrids v2.0 style
        if isinstance(data, dict) and "properties" in data:
            layers = data.get("properties", {}).get("layers", [])
            for layer in layers:
                if layer.get("name") == var_name:
                    for depth in layer.get("depths", []):
                        label = depth.get("label", "")
                        if "0-5" in label or "0-5cm" in label:
                            val = depth.get("values", {}).get("mean")
                            return float(val) if val is not None else None
                    # fallback: first depth
                    if layer.get("depths"):
                        val = layer["depths"][0].get("values", {}).get("mean")
                        return float(val) if val is not None else None
        # OpenLandMap REST style
        result = data.get("result", [{}])[0]
        for key, val in result.items():
            if var_name in key and val is not None and val != 0:
                return float(val)
    except (KeyError, TypeError, IndexError) as exc:
        print(f"    [WARNING] extract_mean error ({var_name}): {exc}")
    return None


def fetch_openlandmap_texture(lat: float, lon: float) -> dict | None:
    """Fallback: sample OpenLandMap STAC COGs for sand/silt/clay at lat/lon.

    Returns values in the same numeric convention as `extract_mean`:
    g/kg * 10 (so 40% -> 400). Returns None if nothing could be sampled.
    """
    if openlandmap is None:
        return None

    stac_base = "https://s3.eu-central-1.wasabisys.com/stac/openlandmap/"
    collections = {
        'sand': 'sand.tot_iso.11277.2020.wpct',
        'silt': 'silt.tot_iso.11277.2020.wpct',
        'clay': 'clay.tot_iso.11277.2020.wpct',
    }

    out = {}
    sess = requests.Session()
    for key, coll in collections.items():
        try:
            coll_url = stac_base + f"{coll}/collection.json"
            r = sess.get(coll_url, timeout=20)
            r.raise_for_status()
            coll_json = r.json()
            # find item href
            item_href = None
            for ln in coll_json.get('links', []):
                if ln.get('rel') == 'item' and ln.get('href'):
                    item_href = ln['href']
                    break
            if not item_href:
                out[key] = None
                continue
            if item_href.startswith('./'):
                item_href = item_href[2:]
            item_url = stac_base + coll + '/' + item_href
            r2 = sess.get(item_url, timeout=20)
            r2.raise_for_status()
            item_json = r2.json()
            # pick asset with 30m and b0cm..30cm if present, else first tiff
            asset_url = None
            for an, ad in item_json.get('assets', {}).items():
                if '30m' in an and 'b0cm' in an and ad.get('href'):
                    asset_url = ad['href']
                    break
            if not asset_url:
                for an, ad in item_json.get('assets', {}).items():
                    href = ad.get('href')
                    if href and 'image/tiff' in (ad.get('type') or ''):
                        asset_url = href
                        break
            if not asset_url:
                out[key] = None
                continue
            val = openlandmap.sample_cog(asset_url, lat, lon)
            if val is None:
                out[key] = None
            else:
                # OpenLandMap values are percent (e.g., 40 -> 40%). Convert to
                # SoilGrids-like convention (g/kg * 10): 40% -> 400
                out[key] = float(val) * 10.0
        except Exception:
            out[key] = None

    if not any(v is not None for v in out.values()):
        return None
    return out

# ══════════════════════════════════════════════════════════════════════════════
# 3. Texture classification (USDA triangle)
# ══════════════════════════════════════════════════════════════════════════════

def normalize_fractions(sand, silt, clay):
    """
    g/kg → % conversion.

    Note: SoilGrids v2.0 values are in g/kg (10 → 1%).
    Threshold updated to > 10 (instead of > 100) so all g/kg values are
    correctly normalised. Rescaling by total is also applied — the sum of
    API values may not be exactly 1000.
    """
    if any(v is None for v in (sand, silt, clay)):
        return None, None, None

    # g/kg format: values are multiplied by 10 (not percent)
    if sand > 10:
        sand /= 10.0
        silt /= 10.0
        clay /= 10.0

    # Normalize by total (correct API rounding errors)
    total = sand + silt + clay
    if total > 0 and abs(total - 100.0) > 1.0:
        sand = sand / total * 100
        silt = silt / total * 100
        clay = clay / total * 100

    return sand, silt, clay


def classify_texture(sand_pct, silt_pct, clay_pct) -> str:
    """USDA texture triangle — full classification."""
    if any(v is None for v in (sand_pct, silt_pct, clay_pct)):
        return "unknown"

    s, si, cl = sand_pct, silt_pct, clay_pct

    if cl >= 40:
        if s >= 45:            return "sandy_clay"
        if si >= 40:           return "silty_clay"
        return "clay"

    if cl >= 27:
        if s >= 45:            return "sandy_clay_loam"
        if si >= 28 and s < 20: return "silty_clay_loam"
        return "clay_loam"

    if si >= 80:
        return "silt" if cl < 12 else "silt_loam"

    if si >= 50:               return "silt_loam"
    if s >= 85:                return "sand"
    if s >= 70:                return "loamy_sand"
    if s >= 52 and cl < 20:   return "sandy_loam"
    return "loam"


# Texture → typical AWC (cm3/cm3) — reference for matching score
# (Saxton & Rawls 2006 pedotransfer function averages)
TEXTURE_AWC = {
    "sand":             0.08,
    "loamy_sand":       0.12,
    "sandy_loam":       0.16,
    "sandy_clay_loam":  0.19,
    "loam":             0.20,
    "silt_loam":        0.22,
    "silt":             0.20,
    "clay_loam":        0.20,
    "silty_clay_loam":  0.22,
    "sandy_clay":       0.18,
    "silty_clay":       0.18,
    "clay":             0.16,
    "unknown":          0.18,
}

# Texture → priority-ordered WOFOST file list
# File properties (SMW / SMFCF → AWC):
#   ec1.new  : AWC=0.070  coarse sand
#   sr1.new  : AWC=0.118  fine sand
#   spg002   : AWC=0.088  light
#   spg003   : AWC=0.113  light-medium
#   m01.awc  : AWC=0.150  coarse texture
#   sr2.new  : AWC=0.176  sandy loam
#   sr4.new  : AWC=0.171  fine sandy loam
#   spg004   : AWC=0.138  medium
#   ec2.new  : AWC=0.173  medium (loam)
#   sr3.new  : AWC=0.205  very loamy fine sand
#   spg005   : AWC=0.163  medium
#   soil_5   : AWC=0.163  medium
#   spg006   : AWC=0.188  medium-fine
#   m02.awc  : AWC=0.220  medium texture
#   ec3.new  : AWC=0.196  medium-fine
#   spg007   : AWC=0.213  fine-medium
#   m03.awc  : AWC=0.250  medium-fine texture
#   m04.awc  : AWC=0.190  fine texture
#   ec4.new  : AWC=0.160  fine (clay)
#   m05.awc  : AWC=0.130  very fine texture
#   ec5.new  : AWC=0.084  very fine (heavy clay)
#   ec6.new  : AWC=0.392  peat/organic
TEXTURE_PRIORITY: dict[str, list[str]] = {
    "sand":             ["ec1.new",  "sr1.new",  "spg002.awc", "m01.awc"],
    "loamy_sand":       ["sr1.new",  "sr2.new",  "ec1.new",    "spg003.awc", "m01.awc"],
    "sandy_loam":       ["sr2.new",  "sr4.new",  "ec2.new",    "sr3.new",    "spg004.awc", "m02.awc"],
    "loam":             ["ec2.new",  "sr3.new",  "sr4.new",    "m02.awc",    "spg005.awc", "soil_5.sol"],
    "sandy_clay_loam":  ["ec2.new",  "m02.awc",  "spg005.awc", "sr4.new",    "spg006.awc"],
    "silt_loam":        ["ec3.new",  "m03.awc",  "sr3.new",    "spg006.awc", "spg007.awc"],
    "silt":             ["ec3.new",  "m03.awc",  "spg007.awc", "spg006.awc"],
    "clay_loam":        ["ec3.new",  "m04.awc",  "m03.awc",    "ec4.new",    "spg007.awc"],
    "silty_clay_loam":  ["ec4.new",  "m04.awc",  "ec3.new",    "m03.awc"],
    "sandy_clay":       ["ec4.new",  "m04.awc",  "ec3.new",    "m05.awc"],
    "silty_clay":       ["ec4.new",  "ec5.new",  "m04.awc",    "m05.awc"],
    "clay":             ["ec4.new",  "ec5.new",  "m04.awc",    "m05.awc"],
    "unknown":          ["ec2.new",  "m02.awc",  "spg005.awc", "soil_5.sol"],
}


# ══════════════════════════════════════════════════════════════════════════════
# 4. Matching algorithm
# ══════════════════════════════════════════════════════════════════════════════

def score_match(soil: dict, texture: str, target_awc: float) -> tuple[float, dict]:
    """
    Scores how well a WOFOST file matches the given texture/AWC.
    Lower score = better match.

    Components:
        priority_rank : position in TEXTURE_PRIORITY list (× 2.0)
                        if not in list: len(list) + 1 (lowest priority)
        awc_diff      : |AWC_file − AWC_target| × 10.0
    """
    preferred = TEXTURE_PRIORITY.get(texture, [])
    try:
        priority_rank = preferred.index(soil["filename"])
    except ValueError:
        priority_rank = len(preferred) + 1

    file_awc = soil.get("AWC")
    if file_awc is not None:
        awc_diff = abs(file_awc - target_awc)
    else:
        awc_diff = 0.10  # penalty for missing SMFCF

    total = priority_rank * 2.0 + awc_diff * 10.0

    details = {
        "priority_rank": priority_rank,
        "in_priority_list": priority_rank < len(preferred) + 1,
        "awc_diff":         round(awc_diff, 4),
        "total_score":      round(total, 4),
    }
    return total, details


def _fetch_soilgrids_simple(lat: float, lon: float) -> tuple[float | None, float | None, float | None]:
    """
    Single, no-retry SoilGrids request — for neighbour coordinate scanning.
    Returns (sand_raw, silt_raw, clay_raw); (None, None, None) on error/null.
    """
    params = [
        ("lat", float(lat)), ("lon", float(lon)),
        ("property", "sand"), ("property", "silt"), ("property", "clay"),
        ("depth", "0-5cm"), ("value", "mean"),
    ]
    try:
        r = cache_session.get(
            SOILGRIDS_URL, params=params,
            headers={"Accept": "application/json"}, timeout=15,
        )
        if r.status_code != 200:
            return None, None, None
        data = r.json()
        return (
            extract_mean(data, "sand"),
            extract_mean(data, "silt"),
            extract_mean(data, "clay"),
        )
    except Exception:
        return None, None, None


def _find_neighbor_soil(lat: float, lon: float) -> tuple[float | None, float | None, float | None, tuple | None]:
    """
    Scans nearby neighbours when the original coordinate returns null.
    Tries ±0.1° first, then ±0.2° (8 directions, sorted by distance).
    Returns: (sand_raw, silt_raw, clay_raw, (nlat, nlon)) or (None,None,None,None).
    """
    for delta in (0.1, 0.2):
        offsets = [
            (0, delta), (0, -delta), (delta, 0), (-delta, 0),
            (delta, delta), (delta, -delta), (-delta, delta), (-delta, -delta),
        ]
        for dlat, dlon in offsets:
            nlat = round(lat + dlat, 4)
            nlon = round(lon + dlon, 4)
            s, si, cl = _fetch_soilgrids_simple(nlat, nlon)
            if s is not None or si is not None or cl is not None:
                return s, si, cl, (nlat, nlon)
    return None, None, None, None


def match_soil(lat: float, lon: float, soil_files: list[dict]) -> dict:
    """
    Fetches data from SoilGrids for a single coordinate and finds the best matching file.
    """
    print(f"\n  📍 lat={lat}, lon={lon}")

    # ── 1. Original coordinate ──
    resp       = fetch_soilgrids(lat, lon)
    from_cache = getattr(resp, "from_cache", False) if resp else True

    sand_raw = extract_mean(resp, "sand")
    silt_raw = extract_mean(resp, "silt")
    clay_raw = extract_mean(resp, "clay")
    print(f"    [DEBUG] Raw API values → sand={sand_raw}, silt={silt_raw}, clay={clay_raw}")

    neighbor_used = None

    all_none = (sand_raw is None and silt_raw is None and clay_raw is None)
    if all_none:
        # ── 2. Neighbour coordinate scan (±0.1° → ±0.2°) ──
        ns, nsi, ncl, neighbor_used = _find_neighbor_soil(lat, lon)
        if neighbor_used:
            print(f"    [NEIGHBOUR] Data retrieved from ({neighbor_used[0]}, {neighbor_used[1]})")
            sand_raw, silt_raw, clay_raw = ns, nsi, ncl
        else:
            # ── 3. OpenLandMap STAC COG fallback ──
            olm = fetch_openlandmap_texture(lat, lon)
            if olm:
                print("    [FALLBACK] Using OpenLandMap data")
                sand_raw = olm.get('sand')
                silt_raw = olm.get('silt')
                clay_raw = olm.get('clay')

    sand, silt, clay = normalize_fractions(sand_raw, silt_raw, clay_raw)
    texture          = classify_texture(sand, silt, clay)
    target_awc       = TEXTURE_AWC.get(texture, 0.18)

    if resp is None:
        print("    [WARNING] SoilGrids did not respond. Texture='unknown', AWC=0.18")
    else:
        s  = sand  or 0
        si = silt  or 0
        cl = clay  or 0
        print(f"    SoilGrids → sand={s:.1f}%  silt={si:.1f}%  clay={cl:.1f}%")
        print(f"    Texture: {texture}  |  Target AWC: {target_awc:.3f} cm³/cm³"
              + ("  [cache]" if from_cache else "  [API]"))

    # ── Scoring ──
    scored = sorted(
        [(score_match(sf, texture, target_awc), sf) for sf in soil_files],
        key=lambda x: x[0][0],
    )

    (best_score, best_details), best = scored[0]

    in_list = best_details.get("in_priority_list", False)
    rank_str = f"priority#{best_details.get('priority_rank', '?')}" if in_list else "not in list"
    print(f"    ✅ Best: {best['filename']}  |  SOLNAM: {best.get('SOLNAM','?')}"
          f"  |  {rank_str}  |  score={best_score:.4f}")

    return {
        "sg_sand_pct":    round(sand, 2)  if sand  is not None else None,
        "sg_silt_pct":    round(silt, 2)  if silt  is not None else None,
        "sg_clay_pct":    round(clay, 2)  if clay  is not None else None,
        "texture_class":  texture,
        "target_awc":     target_awc,
        "sg_available":   resp is not None,
        "sg_from_cache":  from_cache,
        "neighbor_coord": list(neighbor_used) if neighbor_used else None,
        "best_match": {
            "filename": best["filename"],
            "SOLNAM":   best.get("SOLNAM"),
            "filepath": best["filepath"],
            "SMW":      best.get("SMW"),
            "SMFCF":    best.get("SMFCF"),
            "SM0":      best.get("SM0"),
            "CRAIRC":   best.get("CRAIRC"),
            "AWC":      best.get("AWC"),
            "score":    round(best_score, 4),
        },
        "alternatives": [
            {
                "filename": sf["filename"],
                "SOLNAM":   sf.get("SOLNAM"),
                "AWC":      sf.get("AWC"),
                "score":    round(sc, 4),
            }
            for (sc, _), sf in scored[1:4]
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. Batch processing
# ══════════════════════════════════════════════════════════════════════════════

def batch_match(locations: list[dict]) -> list[dict]:
    """
    Batch matching for multiple coordinates.

    locations format:
        [{"name": "Izmir", "lat": 38.43, "lon": 27.41}, ...]
    """
    # ── Load files ──
    print(f"📂 Loading soil files: {SOIL_FILES_DIR}")
    soil_files = load_soil_files(SOIL_FILES_DIR)
    print(f"   {len(soil_files)} files loaded:\n")

    col = "{:<25} {:<40} {:>6} {:>7} {:>6}"
    print(col.format("File", "SOLNAM", "SMW", "SMFCF", "AWC"))
    print("─" * 90)
    for sf in soil_files:
        print(col.format(
            sf["filename"][:24],
            sf.get("SOLNAM", "")[:39],
            str(sf.get("SMW",   "?")),
            str(sf.get("SMFCF", "?")),
            str(sf.get("AWC",   "?")),
        ))

    # ── Match ──
    results = []
    total   = len(locations)

    for i, loc in enumerate(locations, 1):
        lat  = loc.get("lat") or loc.get("latitude")
        lon  = loc.get("lon") or loc.get("longitude")
        name = loc.get("name", f"location_{i}")

        print(f"\n{'═'*60}")
        print(f"[{i}/{total}] {name}")

        if lat is None or lon is None:
            print("  [ERROR] Coordinate missing, skipped.")
            continue

        result        = match_soil(lat, lon, soil_files)
        result["lat"] = lat
        result["lon"] = lon
        result["name"]= name
        results.append(result)

        # Respect API rate limit on cache miss
        if not result.get("sg_from_cache") and i < total:
            print(f"    API call made — waiting {API_SLEEP_SECONDS}s...")
            time.sleep(API_SLEEP_SECONDS)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 6. Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    # ── Coordinate list — add your own locations here ───────────────────────
    locations = [
        {"name": "Izmir - Kemalpasa Plain",   "lat": 38.43, "lon": 27.41},
        {"name": "Konya Plain",               "lat": 37.87, "lon": 32.48},
        {"name": "Thrace - Edirne",           "lat": 41.67, "lon": 26.56},
        {"name": "Cukurova - Adana",          "lat": 37.00, "lon": 35.32},
        {"name": "Gediz Basin - Alasehir",    "lat": 38.35, "lon": 28.51},
    ]

    results = batch_match(locations)

    # ── Summary table ──────────────────────────────────────────────────────
    print(f"\n{'═'*90}")
    print("SUMMARY TABLE")
    print(f"{'═'*90}")
    hdr = "{:<30} {:<16} {:>8}  {:<22} {}"
    print(hdr.format("Location", "Texture", "AWC ref.", "Best file", "SOLNAM"))
    print("─" * 90)
    for r in results:
        bm = r["best_match"]
        print(hdr.format(
            r["name"][:29],
            r["texture_class"],
            f"{r['target_awc']:.3f}",
            bm["filename"][:21],
            bm.get("SOLNAM", ""),
        ))

    # ── Save JSON ──────────────────────────────────────────────────────────
    with open(OUTPUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n💾 Results saved: {OUTPUT_JSON}")