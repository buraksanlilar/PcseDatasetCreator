"""
Merges yearly simulation parquet files into a single dataset.

Run this after simulation/simulation.py has finished.

Usage:
    python scripts/merge_dataset.py
"""

import os
import sys
import glob
import pandas as pd

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))

yearly_dir = os.path.join(project_root, "output", "yearly")
output_file = os.path.join(project_root, "output", "final_hourly_pcse_dataset_multiyear.parquet")

files = sorted(glob.glob(os.path.join(yearly_dir, "pcse_*.parquet")))

if not files:
    print(f"No yearly parquet files found in '{yearly_dir}'.")
    sys.exit(1)

print(f"Found {len(files)} yearly files. Merging...")

frames = []
for f in files:
    year = os.path.basename(f).replace("pcse_", "").replace(".parquet", "")
    print(f"  Loading {year}...")
    frames.append(pd.read_parquet(f))

print("Concatenating...")
final = pd.concat(frames, ignore_index=True)

print(f"Saving to '{output_file}'...")
final.to_parquet(output_file, index=False)

size_gb = os.path.getsize(output_file) / (1024 ** 3)
print(f"Done. {len(final):,} rows — {size_gb:.2f} GB")
