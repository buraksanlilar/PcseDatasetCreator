"""WAV provider for PCSE/WOFOST site configuration.

Düzeltmeler ve iyileştirmeler:
  1. WAV hesabında m³/m³ × cm → cm dönüşümü düzeltildi
  2. 81-100 cm arası için Open-Meteo'nun son katmanı extrapolate edildi
  3. SoilGrids label formatı normalizasyonu eklendi
  4. Katman ağırlıklı ortalama ile daha doğru eşleştirme
  5. SoilGrids null koordinat için nearest-neighbor fallback eklendi
  6. eksik katmanlarda clay/sand/soc PTF (Saxton-Rawls 2006) ile
      field capacity / başlangıç nemi fallback'i
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Dict, Iterable, List

import requests
import requests_cache

logger = logging.getLogger(__name__)

script_dir = os.path.dirname(os.path.abspath(__file__))
cache_session = requests_cache.CachedSession(
    os.path.join(script_dir, ".wav_cache"),
    expire_after=-1,
)

OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

# (api_name, top_cm, bot_cm)
OPENMETEO_LAYERS = [
    ("soil_moisture_0_to_1cm",    0,  1),
    ("soil_moisture_1_to_3cm",    1,  3),
    ("soil_moisture_3_to_9cm",    3,  9),
    ("soil_moisture_9_to_27cm",   9, 27),
    ("soil_moisture_27_to_81cm", 27, 81),
]

# (label, top_cm, bot_cm, thickness_cm)
SOILGRIDS_DEPTHS = [
    ("0-5cm",    0,   5,  5),
    ("5-15cm",   5,  15, 10),
    ("15-30cm", 15,  30, 15),
    ("30-60cm", 30,  60, 30),
    ("60-100cm",60, 100, 40),
]

_SOILGRIDS_LABEL_ALIASES = {
    "0-5":   "0-5cm",
    "5-15":  "5-15cm",
    "15-30": "15-30cm",
    "30-60": "30-60cm",
    "60-100":"60-100cm",
}

# Nearest-neighbor arama parametreleri
_NN_STEP_DEG = 0.1   # Her adımda kaç derece genişleyelim
_NN_MAX_DEG  = 0.5   # Maksimum arama yarıçapı (derece) ≈ 55 km


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------

def _request_json(
    session: requests.Session,
    url: str,
    params: dict,
    timeout: int,
    attempts: int = 3,
) -> dict | None:
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code == 429 and attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if attempt >= attempts:
                logger.warning("request failed for %s: %s", url, exc)
                return None
            time.sleep(2 ** attempt)
    return None


def _normalize_soilgrids_label(raw_label: str) -> str:
    """'0-5' → '0-5cm' gibi format farklarını normalize eder."""
    label = str(raw_label).strip()
    return _SOILGRIDS_LABEL_ALIASES.get(label, label)


def _soilgrids_coord_has_data(lat: float, lon: float) -> bool:
    """
    Verilen koordinatta SoilGrids'in clay verisi var mı diye hızlıca kontrol eder.
    Sadece ilk depth katmanına bakar — yeterli.
    """
    payload = _request_json(
        cache_session,
        SOILGRIDS_URL,
        {"lon": lon, "lat": lat, "property": "clay", "value": "mean"},
        timeout=30,
    )
    if not payload:
        return False
    try:
        val = payload["properties"]["layers"][0]["depths"][0]["values"]["mean"]
        return val is not None
    except (KeyError, IndexError, TypeError):
        return False


def _find_nearest_valid_soilgrids(lat: float, lon: float) -> tuple[float, float]:
    """
    Merkez koordinat SoilGrids'te boşsa, spiral genişleyen grid'de
    ilk geçerli noktayı bulur.

    Dönüş: (valid_lat, valid_lon) — merkez geçerliyse orijinali döner.
    """
    # Merkezi önce dene
    if _soilgrids_coord_has_data(lat, lon):
        return lat, lon

    logger.info(
        "SoilGrids (%.4f, %.4f) boş — nearest-neighbor aranıyor", lat, lon
    )

    r = _NN_STEP_DEG
    best_lat, best_lon, best_dist = None, None, float("inf")

    while r <= _NN_MAX_DEG + 1e-9:
        candidates = set()
        for dlat in [-r, 0.0, r]:
            for dlon in [-r, 0.0, r]:
                if dlat == 0.0 and dlon == 0.0:
                    continue
                candidates.add((round(dlat, 2), round(dlon, 2)))

        for dlat, dlon in candidates:
            clat = round(lat + dlat, 4)
            clon = round(lon + dlon, 4)
            dist = math.sqrt(dlat ** 2 + dlon ** 2)
            if dist >= best_dist:
                continue
            if _soilgrids_coord_has_data(clat, clon):
                best_lat, best_lon, best_dist = clat, clon, dist
                logger.info(
                    "  ✓ Geçerli nokta: (%.4f, %.4f) — orijinalden ~%.1f km",
                    clat, clon, dist * 111,
                )
                break
        else:
            r = round(r + _NN_STEP_DEG, 2)
            continue
        break   # geçerli nokta bulundu, aramayı durdur

    if best_lat is None:
        logger.warning(
            "%.1f derece yarıçap içinde geçerli SoilGrids noktası bulunamadı, "
            "orijinal koordinat kullanılıyor (PTF devreye girecek)",
            _NN_MAX_DEG,
        )
        return lat, lon

    return best_lat, best_lon


# ---------------------------------------------------------------------------
# Saxton-Rawls (2006) Pedotransfer Function
# ---------------------------------------------------------------------------

def _saxton_rawls_wp(clay_pct: float, sand_pct: float, soc_pct: float = 0.5) -> float:
    """
    Saxton & Rawls (2006) PTF ile wilting point tahmini (m³/m³).

    clay_pct : 0-100 yüzde
    sand_pct : 0-100 yüzde
    soc_pct  : organik karbon yüzdesi (varsayılan 0.5)
    """
    S  = sand_pct / 100.0
    C  = clay_pct / 100.0
    OM = soc_pct  / 100.0 * 1.724   # SOC → OM dönüşümü

    theta_1500t = (
        -0.024 * S
        + 0.487 * C
        + 0.006 * OM
        + 0.005 * (S * OM)
        - 0.013 * (C * OM)
        + 0.068 * (S * C)
        - 0.031
    )
    theta_wp = theta_1500t + (0.14 * theta_1500t - 0.02)
    return round(max(0.01, min(theta_wp, 0.60)), 4)


def _saxton_rawls_fc(clay_pct: float, sand_pct: float, soc_pct: float = 0.5) -> float:
    """
    Saxton & Rawls (2006) PTF ile field capacity tahmini (m³/m³).

    clay_pct : 0-100 yüzde
    sand_pct : 0-100 yüzde
    soc_pct  : organik karbon yüzdesi (varsayılan 0.5)
    """
    S = sand_pct / 100.0
    C = clay_pct / 100.0
    OM = soc_pct / 100.0 * 1.724

    theta_33t = (
        -0.251 * S
        + 0.195 * C
        + 0.011 * OM
        + 0.006 * (S * OM)
        - 0.027 * (C * OM)
        + 0.452 * (S * C)
        + 0.299
    )
    theta_fc = theta_33t + (1.283 * (theta_33t ** 2) - 0.374 * theta_33t - 0.015)
    return round(max(0.02, min(theta_fc, 0.65)), 4)


def _fetch_soilgrids_texture(lat: float, lon: float) -> Dict[str, Dict[str, float]]:
    """
    clay, sand, soc değerlerini her derinlik için çeker.
    Dönüş: {"0-5cm": {"clay": 25.0, "sand": 40.0, "soc": 0.8}, ...}
    """
    # Fallback: killi-tınlı Akdeniz toprağı ortalaması
    result: Dict[str, Dict[str, float]] = {
        label: {"clay": 30.0, "sand": 35.0, "soc": 0.5}
        for label, *_ in SOILGRIDS_DEPTHS
    }

    for prop, divisor in [("clay", 10.0), ("sand", 10.0), ("soc", 10.0)]:
        payload = _request_json(
            cache_session,
            SOILGRIDS_URL,
            {"lon": lon, "lat": lat, "property": prop, "value": "mean"},
            timeout=30,
        )
        if not payload:
            logger.warning("SoilGrids %s çekilemedi, default kullanılıyor", prop)
            continue

        for layer in payload.get("properties", {}).get("layers", []):
            if layer.get("name") != prop:
                continue
            for depth_info in layer.get("depths", []):
                label = _normalize_soilgrids_label(depth_info.get("label", ""))
                raw   = depth_info.get("values", {}).get("mean")
                if label in result and raw is not None:
                    result[label][prop] = raw / divisor   # → yüzde

    return result


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------

def _fetch_openmeteo(lat: float, lon: float, reference_date: str | None = None) -> Dict[str, float | None]:
    params = {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    ",".join(name for name, *_ in OPENMETEO_LAYERS),
        "timezone":  "auto",
    }
    if reference_date is not None:
        params["start_date"] = reference_date
        params["end_date"] = reference_date
        payload = _request_json(cache_session, OPENMETEO_ARCHIVE_URL, params, timeout=30)
    else:
        params["forecast_days"] = 1
        payload = _request_json(cache_session, OPENMETEO_URL, params, timeout=20)

    if not payload:
        return {name: None for name, *_ in OPENMETEO_LAYERS}

    hourly = payload.get("hourly", {})
    result: Dict[str, float | None] = {}
    for name, *_ in OPENMETEO_LAYERS:
        values = hourly.get(name, [])
        result[name] = next((v for v in values if v is not None), None)
    return result


# ---------------------------------------------------------------------------
# SoilGrids — WP
# ---------------------------------------------------------------------------

def _fetch_soilgrids_wp_rest(lat: float, lon: float) -> Dict[str, float]:
    """
    wv1500'i doğrudan çeker.
    Null/boş gelirse {} döner → çağıran PTF'e geçer.
    """
    payload = _request_json(
        cache_session,
        SOILGRIDS_URL,
        {"lon": lon, "lat": lat, "property": "wv1500", "value": "mean"},
        timeout=30,
    )
    if not payload:
        logger.warning("SoilGrids REST yanıt vermedi")
        return {}

    result: Dict[str, float] = {}
    known_labels = {lbl for lbl, *_ in SOILGRIDS_DEPTHS}
    for layer in payload.get("properties", {}).get("layers", []):
        if layer.get("name") != "wv1500":
            continue
        for depth_info in layer.get("depths", []):
            label = _normalize_soilgrids_label(depth_info.get("label", ""))
            raw   = depth_info.get("values", {}).get("mean")
            
            if label not in known_labels:
                continue
            if raw is not None:
                result[label] = raw / 1000.0   # cm³/cm³ × 1000 → m³/m³

    return result   # boş dict → PTF tetiklenir


def _fetch_soilgrids_wp_package(lat: float, lon: float) -> Dict[str, float]:
    try:
        from soilgrids import SoilGrids
    except ImportError:
        return _fetch_soilgrids_wp_rest(lat, lon)

    try:
        sg = SoilGrids()
        result: Dict[str, float] = {}
        for label, *_ in SOILGRIDS_DEPTHS:
            data = sg.get_coverage_data(
                service_id="wv1500",
                coverage_id=f"wv1500_{label}_mean",
                west=lon - 0.01, south=lat - 0.01,
                east=lon + 0.01, north=lat + 0.01,
                crs="urn:ogc:def:crs:EPSG::4326",
                output=None,
            )
            result[label] = float(getattr(data, "values", data).mean()) / 1000.0
        return result
    except Exception as exc:
        logger.warning("soilgrids package failed, falling back to REST: %s", exc)
        return _fetch_soilgrids_wp_rest(lat, lon)


def _fetch_soilgrids_wp(lat: float, lon: float, use_package: bool = True) -> Dict[str, float]:
    """
    WP alma stratejisi (sırasıyla):
      1. Nearest-neighbor ile geçerli SoilGrids koordinatını bul
      2. wv1500 dene (REST veya package)
      3. wv1500 null/boşsa → clay/sand/soc'tan Saxton-Rawls PTF
      4. Hepsi başarısızsa → sabit fallback (0.10)
    """
    # 1. Geçerli koordinatı bul (boşsa yakını ara)
    valid_lat, valid_lon = _find_nearest_valid_soilgrids(lat, lon)

    # 2. wv1500 dene
    if use_package:
        wp = _fetch_soilgrids_wp_package(valid_lat, valid_lon)
    else:
        wp = _fetch_soilgrids_wp_rest(valid_lat, valid_lon)

    # Tüm katmanlar doluysa kullan
    if len(wp) == len(SOILGRIDS_DEPTHS):
        logger.info("WP: wv1500'den alındı (%.4f, %.4f)", valid_lat, valid_lon)
        return wp

    # 3. wv1500 yetersiz → Saxton-Rawls PTF
    logger.info(
        "wv1500 yetersiz (%d/%d katman), Saxton-Rawls PTF devreye giriyor",
        len(wp), len(SOILGRIDS_DEPTHS),
    )
    texture = _fetch_soilgrids_texture(valid_lat, valid_lon)

    ptf_wp: Dict[str, float] = {}
    for label, *_ in SOILGRIDS_DEPTHS:
        t    = texture.get(label, {})
        clay = t.get("clay", 30.0)
        sand = t.get("sand", 35.0)
        soc  = t.get("soc",  0.5)
        wp_val = _saxton_rawls_wp(clay, sand, soc)
        ptf_wp[label] = wp_val
        logger.debug(
            "PTF WP %s: clay=%.1f sand=%.1f soc=%.2f → θ_wp=%.3f",
            label, clay, sand, soc, wp_val,
        )

    # 4. PTF de boşsa → sabit fallback
    if not ptf_wp:
        logger.warning("PTF de başarısız, sabit WP=0.10 kullanılıyor")
        return {label: 0.10 for label, *_ in SOILGRIDS_DEPTHS}

    return ptf_wp


# ---------------------------------------------------------------------------
# Profil & WAV hesabı
# ---------------------------------------------------------------------------

def _build_profile(
    om_moisture: Dict[str, float | None],
    sg_wp: Dict[str, float],
    sg_texture: Dict[str, Dict[str, float]],
    max_depth_cm: int = 100,
    fill_fraction: float = 0.7,
) -> tuple[list[float], list[float], list[float]]:
    """
    Her santimetre için theta (m³/m³) ve theta_wp (m³/m³) dizilerini oluşturur.

    Open-Meteo 81 cm'de bitiyor; 81-100 cm arasını PTF tabanlı fallback ile dolduruyoruz.
    """
    theta    = [0.0] * max_depth_cm
    theta_wp = [0.0] * max_depth_cm
    theta_fc = [0.0] * max_depth_cm

    # --- Soil moisture (Open-Meteo) ---
    last_valid_val: float | None = None
    for name, top, bot in OPENMETEO_LAYERS:
        val = om_moisture.get(name)
        if val is not None:
            last_valid_val = val
        fill = val if val is not None else last_valid_val
        if fill is None:
            continue
        for cm in range(max(0, top), min(bot, max_depth_cm)):
            theta[cm] = float(fill)

    # --- Wilting point (SoilGrids / PTF) ---
    for label, top, bot, _ in SOILGRIDS_DEPTHS:
        texture = sg_texture.get(label, {})
        clay = float(texture.get("clay", 30.0))
        sand = float(texture.get("sand", 35.0))
        soc = float(texture.get("soc", 0.5))

        fc_est = _saxton_rawls_fc(clay, sand, soc)
        wp_est = sg_wp.get(label)
        if wp_est is None:
            wp_est = _saxton_rawls_wp(clay, sand, soc)

        theta_fc_layer = fc_est
        theta_init_layer = max(wp_est, min(theta_fc_layer, wp_est + fill_fraction * (theta_fc_layer - wp_est)))

        for cm in range(max(0, top), min(bot, max_depth_cm)):
            theta_wp[cm] = float(wp_est)
            theta_fc[cm] = float(theta_fc_layer)
            if theta[cm] == 0.0:
                theta[cm] = float(theta_init_layer)

    # 81-100 cm: OM verisi yoksa PTF tabanlı başlangıç nemi kullan
    if last_valid_val is not None:
        for cm in range(81, max_depth_cm):
            if theta[cm] == 0.0:
                theta[cm] = float(last_valid_val)

    for cm in range(max_depth_cm):
        if theta[cm] == 0.0:
            # OM tamamen eksikse güvenli fallback
            theta[cm] = theta_fc[cm] * fill_fraction if theta_fc[cm] > 0 else 0.20

    return theta, theta_wp, theta_fc


def _compute_wav(
    om_moisture: Dict[str, float | None],
    sg_wp: Dict[str, float],
    sg_texture: Dict[str, Dict[str, float]],
    max_depth_cm: int = 100,
) -> float:
    """
    WAV (cm) = Σ θ_i × Δz    [Δz = 1 cm]

    Bu değer WOFOST/WOFOST72SiteDataProvider için başlangıç toprak su stoğu.
    """
    theta, theta_wp, theta_fc = _build_profile(om_moisture, sg_wp, sg_texture, max_depth_cm)
    # WAV = water in excess of wilting point (cm) — WOFOST tanımı
    wav = sum(max(0.0, theta[i] - theta_wp[i]) for i in range(max_depth_cm))
    return round(min(max(wav, 0.0), 100.0), 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_wav(
    lat: float,
    lon: float,
    max_depth_cm: int = 100,
    use_soilgrids_package: bool = True,
    reference_date: str | None = None,
) -> float:
    """Bir lokasyon için WAV (cm) döndürür."""
    om = _fetch_openmeteo(lat, lon, reference_date=reference_date)
    wp = _fetch_soilgrids_wp(lat, lon, use_package=use_soilgrids_package)
    texture = _fetch_soilgrids_texture(lat, lon)

    logger.debug(
        "OM moisture: %s",
        {k: round(v, 3) for k, v in om.items() if v is not None},
    )
    logger.debug("SoilGrids/PTF WP: %s", {k: round(v, 3) for k, v in wp.items()})
    logger.debug("SoilGrids texture: %s", texture)

    wav = _compute_wav(om, wp, texture, max_depth_cm=max_depth_cm)
    logger.info("WAV(%.4f, %.4f) = %.2f cm", lat, lon, wav)
    return wav


def get_site_data(
    lat: float,
    lon: float,
    max_depth_cm: int = 100,
    use_soilgrids_package: bool = True,
    reference_date: str | None = None,
    smlim: float = 0.4,
    ssi: float = 0.0,
    ssmmax: float = 0.0,
    ifunrn: int = 0,
    notinf: float = 0.0,
) -> Dict[str, float | int]:
    """PCSE-uyumlu site dict döndürür. WAV cm cinsinden float."""
    wav_cm = get_wav(
        lat, lon,
        max_depth_cm=max_depth_cm,
        use_soilgrids_package=use_soilgrids_package,
        reference_date=reference_date,
    )
    return {
        "WAV":    round(float(wav_cm), 2),
        "SMLIM":  float(smlim),
        "SSI":    float(ssi),
        "SSMAX":  float(ssmmax),
        "IFUNRN": int(ifunrn),
        "NOTINF": float(notinf),
    }


def fetch_batch_wavs(
    locations: Iterable[dict],
    max_depth_cm: int = 100,
    use_soilgrids_package: bool = True,
    reference_date: str | None = None,
) -> List[float | None]:
    result: List[float | None] = []
    for loc in locations:
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            result.append(None)
            continue
        try:
            result.append(
                get_wav(
                    float(lat), float(lon),
                    max_depth_cm=max_depth_cm,
                    use_soilgrids_package=use_soilgrids_package,
                    reference_date=reference_date,
                )
            )
        except Exception as exc:
            logger.warning("WAV hesabı başarısız (%s,%s): %s", lat, lon, exc)
            result.append(None)
    return result