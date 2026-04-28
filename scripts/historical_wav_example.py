"""Small helper to compute historical WAV for a single location.

Defaults to a date that already produced a higher WAV for the sample location
used during validation, but you can override the date and coordinates via CLI.
"""

from __future__ import annotations

import argparse
import os
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from openmeteo.wav_provider import get_site_data, get_wav


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute historical WAV for a single location")
    parser.add_argument("--lat", type=float, default=39.836561, help="Latitude")
    parser.add_argument("--lon", type=float, default=26.475204, help="Longitude")
    parser.add_argument("--date", type=str, default="2024-04-15", help="Reference date in YYYY-MM-DD format")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    wav = get_wav(args.lat, args.lon, reference_date=args.date)
    site = get_site_data(args.lat, args.lon, reference_date=args.date)

    print(f"location: ({args.lat}, {args.lon})")
    print(f"reference_date: {args.date}")
    print(f"WAV_cm: {wav:.2f}")
    print(f"site_data: {site}")


if __name__ == "__main__":
    main()