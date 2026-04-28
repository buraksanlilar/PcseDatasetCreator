"""Generate a lightweight dataset pool of locations × reference_dates with site parameters.

Usage examples:
  # quick sample (5 locations × 3 dates)
  python3 scripts/generate_dataset.py --sample

  # full grid, pick best date per location from given candidates
  python3 scripts/generate_dataset.py --dates 2024-01-01,2024-04-01,2024-07-01 --best

This script writes CSV to `dataset_output/site_pool_<mode>.csv`.
"""
from __future__ import annotations

import argparse
import os
import csv
from datetime import datetime
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pcseData import theta_multiyear as tm
from openmeteo.wav_provider import get_site_data


def parse_dates(dates_arg: str) -> List[str]:
    return [d.strip() for d in dates_arg.split(",") if d.strip()]


def build_prototypes(mode: str, sample_n: int = 5):
    if mode == "random":
        coords = tm.build_random_coordinates()
    else:
        coords = tm.build_grid_coordinates()

    prototypes = []
    for i, c in enumerate(coords):
        prototypes.append({
            "location_id": f"loc_{i+1:04d}",
            "latitude": c["latitude"],
            "longitude": c["longitude"],
            "soil_file": tm.soil_files[i % len(tm.soil_files)],
        })

    # sample reduce
    if sample_n and len(prototypes) > sample_n:
        prototypes = prototypes[:sample_n]

    # fetch elevations in batch
    elevations = tm.fetch_batch_elevations(prototypes)
    for proto, elev in zip(prototypes, elevations):
        proto["elevation"] = elev if elev is not None else 100.0

    return prototypes


def generate_pool(prototypes, dates: List[str], best_only: bool = False, out_path: str = None):
    rows = []
    for proto in prototypes:
        if best_only:
            best_row = None
            best_wav = -1.0
            for d in dates:
                site = get_site_data(proto["latitude"], proto["longitude"], reference_date=d)
                wav = float(site.get("WAV", 0.0))
                if wav > best_wav:
                    best_wav = wav
                    best_row = {**proto, "reference_date": d, **site}
            if best_row:
                rows.append(best_row)
        else:
            for d in dates:
                site = get_site_data(proto["latitude"], proto["longitude"], reference_date=d)
                row = {**proto, "reference_date": d, **site}
                rows.append(row)

    if out_path is None:
        out_path = os.path.join(ROOT, "dataset_output", f"site_pool_{'best' if best_only else 'all'}.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if rows:
        keys = list(rows[0].keys())
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=keys)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

    return out_path, len(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sample", action="store_true", help="Run a small sample (fast)")
    p.add_argument("--mode", choices=("grid", "random"), default="grid")
    p.add_argument("--dates", type=str, default=None, help="Comma-separated YYYY-MM-DD dates to probe")
    p.add_argument("--best", action="store_true", help="Keep only the date with max WAV per location")
    p.add_argument("--out", type=str, default=None, help="Output CSV path")
    args = p.parse_args()

    if args.sample:
        dates = ["2024-01-01", "2024-04-01", "2024-07-01"]
        prototypes = build_prototypes(args.mode, sample_n=5)
    else:
        if args.dates:
            dates = parse_dates(args.dates)
        else:
            # default: first day of each quarter 2024
            dates = ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"]
        prototypes = build_prototypes(args.mode, sample_n=None)

    print(f"Probing {len(prototypes)} locations × {len(dates)} dates (best_only={args.best})")
    out_path, count = generate_pool(prototypes, dates, best_only=args.best, out_path=args.out)
    print(f"Wrote {count} rows → {out_path}")


if __name__ == "__main__":
    main()
