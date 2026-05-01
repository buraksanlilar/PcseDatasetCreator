"""Dry-run script for testing `openmeteo/soil_matcher.py`.
This version uses the module's real `fetch_soilgrids` implementation
so SoilGrids API is called (ensure network and fair-use limits).
"""

import os
import sys
import json
import glob
import argparse
import shutil
import requests

# Ensure project root is on sys.path so `import openmeteo` works when running
# this script directly from `scripts/` or other working directories.
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from providers import soil_matcher



def main():
    # NOTE: No monkeypatch — this will call SoilGrids via
    # `soil_matcher.fetch_soilgrids`. Be mindful of API fair-use limits.

    parser = argparse.ArgumentParser(description='Dry-run soil matcher (optional cache controls)')
    parser.add_argument('--no-cache', action='store_true', help='Disable requests_cache for this run')
    parser.add_argument('--clear-cache', action='store_true', help='Remove persistent cache files before running')
    args = parser.parse_args()

    # If requested, clear requests_cache files created by the module
    if args.clear_cache:
        try:
            cache_dir = os.path.join(soil_matcher.script_dir, ".soilgrids_cache")
            patterns = [cache_dir + '*', cache_dir + '.*']
            removed = 0
            for p in patterns:
                for f in glob.glob(p):
                    try:
                        if os.path.isdir(f):
                            shutil.rmtree(f)
                        else:
                            os.remove(f)
                        removed += 1
                    except Exception:
                        pass
            print(f"[DRY RUN] Cleared {removed} cache files from {soil_matcher.script_dir}")
        except Exception as e:
            print(f"[DRY RUN] Cache clear failed: {e}")

    # Optionally disable caching by replacing the module session
    if args.no_cache:
        try:
            soil_matcher.cache_session = requests.Session()
            print("[DRY RUN] Disabled requests_cache for this run (using direct requests.Session)")
        except Exception as e:
            print(f"[DRY RUN] Failed to disable cache: {e}")

    # Use the soil files directory from the module, but prefer project-level
    # `soilTypes/` if it exists and contains WOFOST soil files.
    soil_dir = soil_matcher.SOIL_FILES_DIR
    repo_root = os.path.abspath(os.path.join(os.path.dirname(soil_matcher.__file__), '..'))
    alt_soil_dir = os.path.join(repo_root, 'soils')
    if os.path.isdir(alt_soil_dir):
        try:
            has_files = any(f.lower().endswith(('.new', '.awc', '.sol')) for f in os.listdir(alt_soil_dir))
        except Exception:
            has_files = False
        if has_files:
            soil_dir = alt_soil_dir

    print(f"Using soil files from: {soil_dir}")

    # Example locations (kept small to be fast)
    locations = [
        {"name": "Test Location 1", "lat": 38.43, "lon": 27.41},
        {"name": "Test Location 2", "lat": 37.87, "lon": 32.48},
    ]

    # Ensure the module's default directory matches our preference so older
    # signatures that don't accept `soil_dir` still use the intended files.
    try:
        results = soil_matcher.batch_match(locations, soil_dir=soil_dir, verbose=True)
    except TypeError:
        print("  [DRY RUN] batch_match signature mismatch — setting module SOIL_FILES_DIR and calling with locations only")
        try:
            soil_matcher.SOIL_FILES_DIR = soil_dir
        except Exception:
            pass
        results = soil_matcher.batch_match(locations)

    out_path = os.path.abspath(os.path.join(os.path.dirname(soil_matcher.__file__), '..', 'dataset_output', 'dry_run_soil_match_results.json'))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nDry-run results saved to: {out_path}")


if __name__ == '__main__':
    main()
