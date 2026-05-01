"""
SoilGrids → WOFOST Soil File Matcher
=====================================
Verilen koordinatlar için SoilGrids API'den toprak verisi çeker (sand/silt/clay),
texture class belirler ve soilType klasöründeki WOFOST dosyalarıyla eşleştirir.

Kullanım:
    python soil_matcher.py

Çıktı:
    - Konsola özet tablo
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

# ─── Yapılandırma ─────────────────────────────────────────────────────────────

SOILGRIDS_URL  = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SOIL_FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soils")
OUTPUT_JSON    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soil_match_results.json")

# SoilGrids fair-use: ~5 istek/dakika → cache miss'te 13sn bekle
API_SLEEP_SECONDS = 13


# ══════════════════════════════════════════════════════════════════════════════
# 1. WOFOST dosya okuma
# ══════════════════════════════════════════════════════════════════════════════

def parse_wofost_soil_file(filepath: str) -> dict:
    """
    .new / .awc / .sol formatındaki WOFOST dosyasını okur.
    Döndürür: dict — SOLNAM, SMW, SMFCF, SM0, CRAIRC, K0, SOPE, KSUB, AWC
    """
    result = {
        "filepath": filepath,
        "filename": os.path.basename(filepath),
    }

    with open(filepath, encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    # Yorum satırlarını ve ** başlıklarını temizle
    clean_lines = []
    for line in content.splitlines():
        if "!" in line:
            line = line[: line.index("!")]
        line = line.strip()
        if line and not line.startswith("**"):
            clean_lines.append(line)
    clean = "\n".join(clean_lines)

    # Skalar parametreler
    for key in ("SMW", "SMFCF", "SM0", "CRAIRC", "K0", "SOPE", "KSUB",
                "SPADS", "SPODS", "SPASS", "SPOSS", "DEFLIM"):
        m = re.search(rf"\b{key}\s*=\s*([-\d.]+)", clean)
        if m:
            result[key] = float(m.group(1))

    # SOLNAM (orijinal içerikten, tırnaklar dahil)
    m = re.search(r"SOLNAM\s*=\s*'([^']+)'", content)
    result["SOLNAM"] = m.group(1).strip() if m else result["filename"]

    # AWC = field capacity − wilting point
    smw   = result.get("SMW")
    smfcf = result.get("SMFCF")
    if smw is not None and smfcf is not None:
        result["AWC"] = round(smfcf - smw, 4)

    return result


def load_soil_files(directory: str) -> list[dict]:
    """soilType klasöründeki tüm .new / .awc / .sol dosyalarını yükler."""
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"soilType klasörü bulunamadı: {directory}")

    soils = []
    for fname in sorted(os.listdir(directory)):
        if fname.lower().endswith((".new", ".awc", ".sol")):
            path = os.path.join(directory, fname)
            try:
                soils.append(parse_wofost_soil_file(path))
            except Exception as exc:
                print(f"  [UYARI] {fname} okunamadı: {exc}")

    if not soils:
        raise RuntimeError(f"soilType klasöründe hiç dosya bulunamadı: {directory}")

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
                print(f"    [SoilGrids] Rate limit — {wait}s bekleniyor...")
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                wait = 5 * attempt
                print(f"    [SoilGrids] Server error {r.status_code} — {wait}s bekleniyor...")
                if attempt < max_attempts:
                    time.sleep(wait)
                    continue
                break
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            wait = 8 * attempt
            print(f"    [SoilGrids attempt {attempt}] Timeout — {wait}s bekleniyor...")
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
                print(f"    [OpenLandMap] Rate limit — {wait}s bekleniyor...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 5 * attempt
                print(f"    [OpenLandMap] Sunucu hatası {resp.status_code} — {wait}s bekleniyor...")
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
            print(f"    [OpenLandMap attempt {attempt}] Timeout — {wait}s bekleniyor...")
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
        print(f"    [UYARI] extract_mean hatası ({var_name}): {exc}")
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
# 3. Texture sınıflandırma (USDA üçgeni)
# ══════════════════════════════════════════════════════════════════════════════

def normalize_fractions(sand, silt, clay):
    """
    g/kg → % dönüşümü.

    Düzeltme: SoilGrids v2.0 değerleri g/kg cinsinden (10 → 1%).
    Eşik > 10 olarak güncellendi (> 100 yerine), böylece tüm g/kg
    değerleri doğru normalize edilir. Toplam'a göre yeniden ölçekleme
    de eklendi — API'den gelen değerlerin toplamı tam 1000 olmayabilir.
    """
    if any(v is None for v in (sand, silt, clay)):
        return None, None, None

    # g/kg formatı: değerler % değil, 10 ile çarpılmış
    if sand > 10:
        sand /= 10.0
        silt /= 10.0
        clay /= 10.0

    # Toplama göre normalize et (API yuvarlama hatalarını düzelt)
    total = sand + silt + clay
    if total > 0 and abs(total - 100.0) > 1.0:
        sand = sand / total * 100
        silt = silt / total * 100
        clay = clay / total * 100

    return sand, silt, clay


def classify_texture(sand_pct, silt_pct, clay_pct) -> str:
    """USDA texture üçgeni — tam sınıflandırma."""
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


# Texture → tipik AWC (cm3/cm3) — eşleşme skoru için referans
# (Saxton & Rawls 2006 pedotransfer function ortalamaları)
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

# Texture → tercih sıralı WOFOST dosya listesi
# Dosya özellikleri (SMW / SMFCF → AWC):
#   ec1.new  : AWC=0.070  kaba kum
#   sr1.new  : AWC=0.118  ince kum
#   spg002   : AWC=0.088  hafif
#   spg003   : AWC=0.113  hafif-orta
#   m01.awc  : AWC=0.150  kaba tekstür
#   sr2.new  : AWC=0.176  kumlu tın
#   sr4.new  : AWC=0.171  ince kumlu tın
#   spg004   : AWC=0.138  orta
#   ec2.new  : AWC=0.173  orta (tın)
#   sr3.new  : AWC=0.205  çok tinli ince kum
#   spg005   : AWC=0.163  orta
#   soil_5   : AWC=0.163  orta
#   spg006   : AWC=0.188  orta-ince
#   m02.awc  : AWC=0.220  orta tekstür
#   ec3.new  : AWC=0.196  orta-ince
#   spg007   : AWC=0.213  ince-orta
#   m03.awc  : AWC=0.250  orta-ince tekstür
#   m04.awc  : AWC=0.190  ince tekstür
#   ec4.new  : AWC=0.160  ince (kil)
#   m05.awc  : AWC=0.130  çok ince tekstür
#   ec5.new  : AWC=0.084  çok ince (ağır kil)
#   ec6.new  : AWC=0.392  turba/organik
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
# 4. Eşleme algoritması
# ══════════════════════════════════════════════════════════════════════════════

def score_match(soil: dict, texture: str, target_awc: float) -> tuple[float, dict]:
    """
    Bir WOFOST dosyasının verilen texture/AWC ile uyumunu puanlar.
    Düşük skor = daha iyi eşleme.

    Bileşenler:
        priority_rank : TEXTURE_PRIORITY listesindeki sıra (× 2.0)
                        listede yoksa len(liste) + 1 (en düşük öncelik)
        awc_diff      : |AWC_dosya − AWC_hedef| × 10.0
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
        awc_diff = 0.10  # SMFCF eksikliği cezası

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
    Tek seferlik, retry'sız SoilGrids isteği — komşu koordinat taraması için.
    (sand_raw, silt_raw, clay_raw) döndürür; hata/null durumunda (None, None, None).
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
    Orijinal koordinat null döndürdüğünde yakın komşuları tarar.
    Önce ±0.1°, bulunamazsa ±0.2° dener (8 yön, mesafeye göre sıralı).
    Döndürür: (sand_raw, silt_raw, clay_raw, (nlat, nlon)) veya (None,None,None,None).
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
    Tek bir koordinat için SoilGrids'ten veri çeker ve en iyi dosyayı bulur.
    """
    print(f"\n  📍 lat={lat}, lon={lon}")

    # ── 1. Orijinal koordinat ──
    resp       = fetch_soilgrids(lat, lon)
    from_cache = getattr(resp, "from_cache", False) if resp else True

    sand_raw = extract_mean(resp, "sand")
    silt_raw = extract_mean(resp, "silt")
    clay_raw = extract_mean(resp, "clay")
    print(f"    [DEBUG] Ham API değerleri → sand={sand_raw}, silt={silt_raw}, clay={clay_raw}")

    neighbor_used = None

    all_none = (sand_raw is None and silt_raw is None and clay_raw is None)
    if all_none:
        # ── 2. Komşu koordinat taraması (±0.1° → ±0.2°) ──
        ns, nsi, ncl, neighbor_used = _find_neighbor_soil(lat, lon)
        if neighbor_used:
            print(f"    [KOMŞU] ({neighbor_used[0]}, {neighbor_used[1]}) koordinatından veri alındı")
            sand_raw, silt_raw, clay_raw = ns, nsi, ncl
        else:
            # ── 3. OpenLandMap STAC COG fallback ──
            olm = fetch_openlandmap_texture(lat, lon)
            if olm:
                print("    [FALLBACK] OpenLandMap verisi kullanılıyor")
                sand_raw = olm.get('sand')
                silt_raw = olm.get('silt')
                clay_raw = olm.get('clay')

    sand, silt, clay = normalize_fractions(sand_raw, silt_raw, clay_raw)
    texture          = classify_texture(sand, silt, clay)
    target_awc       = TEXTURE_AWC.get(texture, 0.18)

    if resp is None:
        print("    [UYARI] SoilGrids yanıt vermedi. Texture='unknown', AWC=0.18")
    else:
        s  = sand  or 0
        si = silt  or 0
        cl = clay  or 0
        print(f"    SoilGrids → sand={s:.1f}%  silt={si:.1f}%  clay={cl:.1f}%")
        print(f"    Texture: {texture}  |  Hedef AWC: {target_awc:.3f} cm³/cm³"
              + ("  [cache]" if from_cache else "  [API]"))

    # ── Puanlama ──
    scored = sorted(
        [(score_match(sf, texture, target_awc), sf) for sf in soil_files],
        key=lambda x: x[0][0],
    )

    (best_score, best_details), best = scored[0]

    in_list = best_details.get("in_priority_list", False)
    rank_str = f"öncelik#{best_details.get('priority_rank', '?')}" if in_list else "liste dışı"
    print(f"    ✅ En iyi: {best['filename']}  |  SOLNAM: {best.get('SOLNAM','?')}"
          f"  |  {rank_str}  |  skor={best_score:.4f}")

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
# 5. Toplu işleme
# ══════════════════════════════════════════════════════════════════════════════

def batch_match(locations: list[dict]) -> list[dict]:
    """
    Birden fazla koordinat için toplu eşleme.

    locations formatı:
        [{"name": "İzmir", "lat": 38.43, "lon": 27.41}, ...]
    """
    # ── Dosyaları yükle ──
    print(f"📂 Soil dosyaları yükleniyor: {SOIL_FILES_DIR}")
    soil_files = load_soil_files(SOIL_FILES_DIR)
    print(f"   {len(soil_files)} dosya yüklendi:\n")

    col = "{:<25} {:<40} {:>6} {:>7} {:>6}"
    print(col.format("Dosya", "SOLNAM", "SMW", "SMFCF", "AWC"))
    print("─" * 90)
    for sf in soil_files:
        print(col.format(
            sf["filename"][:24],
            sf.get("SOLNAM", "")[:39],
            str(sf.get("SMW",   "?")),
            str(sf.get("SMFCF", "?")),
            str(sf.get("AWC",   "?")),
        ))

    # ── Eşleştir ──
    results = []
    total   = len(locations)

    for i, loc in enumerate(locations, 1):
        lat  = loc.get("lat") or loc.get("latitude")
        lon  = loc.get("lon") or loc.get("longitude")
        name = loc.get("name", f"lokasyon_{i}")

        print(f"\n{'═'*60}")
        print(f"[{i}/{total}] {name}")

        if lat is None or lon is None:
            print("  [HATA] Koordinat eksik, atlandı.")
            continue

        result        = match_soil(lat, lon, soil_files)
        result["lat"] = lat
        result["lon"] = lon
        result["name"]= name
        results.append(result)

        # Cache miss ise API rate-limit'e saygı göster
        if not result.get("sg_from_cache") and i < total:
            print(f"    API çağrısı yapıldı — {API_SLEEP_SECONDS}s bekleniyor...")
            time.sleep(API_SLEEP_SECONDS)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 6. Giriş noktası
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    # ── Koordinat listesi — buraya kendi lokasyonlarınızı ekleyin ──────────
    locations = [
        {"name": "İzmir - Kemalpaşa Ovası",  "lat": 38.43, "lon": 27.41},
        {"name": "Konya Ovası",               "lat": 37.87, "lon": 32.48},
        {"name": "Trakya - Edirne",           "lat": 41.67, "lon": 26.56},
        {"name": "Çukurova - Adana",          "lat": 37.00, "lon": 35.32},
        {"name": "Gediz Havzası - Alaşehir",  "lat": 38.35, "lon": 28.51},
    ]

    results = batch_match(locations)

    # ── Özet tablo ─────────────────────────────────────────────────────────
    print(f"\n{'═'*90}")
    print("ÖZET TABLO")
    print(f"{'═'*90}")
    hdr = "{:<30} {:<16} {:>8}  {:<22} {}"
    print(hdr.format("Lokasyon", "Texture", "AWC ref.", "En iyi dosya", "SOLNAM"))
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

    # ── JSON kaydet ────────────────────────────────────────────────────────
    with open(OUTPUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n💾 Sonuçlar kaydedildi: {OUTPUT_JSON}")