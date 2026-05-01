"""
Helpers for accessing OpenLandMap data (Zenodo / Wasabi STAC / COGs).

This module provides two small utilities:
 - `download_zenodo_record(record_id, out_dir, pattern=None)` : list files
   in a Zenodo record and download those that match an optional pattern.
 - `sample_cog(url, lat, lon)` : sample a single pixel value from a Cloud
   Optimized GeoTIFF available at `url`. Uses `rasterio` if available, else
   falls back to calling `gdal_translate` + `gdalinfo` if GDAL CLI is present.

These follow the access options described in https://docs.openlandmap.org/#accessing-data
"""

import os
import sys
import math
import json
import shutil
import subprocess
from urllib.parse import urljoin

import requests


def download_zenodo_record(record_id, out_dir, pattern=None, token=None):
    """Download files from a Zenodo record.

    record_id: numeric zenodo record id (int or str)
    out_dir: local directory to save files
    pattern: optional substring to filter filenames
    token: optional Zenodo API token for private records

    Returns list of downloaded file paths.
    """
    api_url = f"https://zenodo.org/api/records/{record_id}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.get(api_url, headers=headers, timeout=60)
    r.raise_for_status()
    meta = r.json()
    files = meta.get("files", [])
    os.makedirs(out_dir, exist_ok=True)
    downloaded = []
    for f in files:
        fname = f.get("filename")
        download_link = f.get("links", {}).get("download")
        if not fname or not download_link:
            continue
        if pattern and pattern not in fname:
            continue
        out_path = os.path.join(out_dir, fname)
        if os.path.exists(out_path):
            downloaded.append(out_path)
            continue
        print(f"Downloading {fname} ...")
        with requests.get(download_link, stream=True, headers=headers, timeout=120) as resp:
            resp.raise_for_status()
            with open(out_path, "wb") as wf:
                shutil.copyfileobj(resp.raw, wf)
        downloaded.append(out_path)
    return downloaded


def _sample_with_rasterio(url, lat, lon):
    import rasterio
    from rasterio.warp import transform
    from rasterio.io import MemoryFile

    with rasterio.Env():
        with rasterio.open(url) as src:
            # transform lon/lat to dataset CRS
            dst_crs = src.crs
            xs, ys = rasterio.warp.transform({'init': 'EPSG:4326'}, dst_crs, [lon], [lat])
            x, y = xs[0], ys[0]
            row, col = src.index(x, y)
            data = src.read(1)
            try:
                val = data[row, col]
            except IndexError:
                val = None
            return val


def _sample_with_gdal(url, lat, lon):
    # Use gdalwarp to reproject a tiny window to EPSG:4326 and gdal_translate to crop
    # This fallback requires gdal_translate/gdalinfo installed.
    try:
        # create a small 3x3 pixel warped VRT around the point
        cmd_vrt = [
            "gdalwarp",
            "-t_srs", "EPSG:4326",
            "-te", str(lon - 0.0005), str(lat - 0.0005), str(lon + 0.0005), str(lat + 0.0005),
            "-ts", "3", "3",
            url,
            "/vsimem/tmp_sample.tif",
        ]
        subprocess.check_call(cmd_vrt, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # read with gdalinfo -mm and parse band min/mean/max? Better to use gdal_translate to a small file
        cmd_info = ["gdalinfo", "/vsimem/tmp_sample.tif"]
        out = subprocess.check_output(cmd_info, stderr=subprocess.STDOUT).decode("utf-8")
        # attempt to parse Band 1 values in out (not robust but a last-resort)
        import re
        m = re.search(r"STATISTICS_MIN=([\-0-9.eE]+).*?STATISTICS_MAX=([\-0-9.eE]+)", out, re.S)
        if m:
            return float(m.group(1))
    except Exception:
        return None


def sample_cog(url, lat, lon):
    """Sample a single band value from a Cloud-Optimized GeoTIFF URL at given lat/lon.

    Returns the band value or None if unavailable.
    """
    # Prefer rasterio if installed
    try:
        return _sample_with_rasterio(url, lat, lon)
    except Exception:
        pass
    # fallback to GDAL CLI
    try:
        return _sample_with_gdal(url, lat, lon)
    except Exception:
        return None


if __name__ == "__main__":
    print("openlandmap helper module. Use functions from code, e.g. sample_cog(url, lat, lon)")
