"""Find the best historical date for a single location by WAV.

The script evaluates a small set of reference dates for the same location and
prints the date with the highest WAV (cm). Use this when you want a wetter
starting point for WOFOST.
"""

from __future__ import annotations

import argparse
import os
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from openmeteo.wav_provider import get_wav


DEFAULT_DATES = [
    "2024-01-15",
    "2024-03-15",
    "2024-04-15",
    "2024-06-15",
    "2024-09-15",
    "2024-11-15",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find the historical date with the highest WAV for one location")
    parser.add_argument("--lat", type=float, default=39.836561, help="Latitude")
    parser.add_argument("--lon", type=float, default=26.475204, help="Longitude")
    parser.add_argument(
        "--dates",
        nargs="*",
        default=DEFAULT_DATES,
        help="Reference dates to compare (YYYY-MM-DD)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    results = []
    for reference_date in args.dates:
        wav = get_wav(args.lat, args.lon, reference_date=reference_date)
        results.append((reference_date, wav))

    results.sort(key=lambda item: item[1], reverse=True)
    best_date, best_wav = results[0]

    print(f"location: ({args.lat}, {args.lon})")
    print("candidate_dates and WAV_cm:")
    for reference_date, wav in results:
        print(f"  {reference_date}: {wav:.2f}")
    print(f"\nbest_date: {best_date}")
    print(f"best_wav_cm: {best_wav:.2f}")


if __name__ == "__main__":
    main()