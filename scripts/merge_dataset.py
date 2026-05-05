"""
Merges yearly simulation parquet files into a single dataset.

Run this after simulation/simulation.py has finished.

Usage:
    python scripts/merge_dataset.py
"""

import os
import sys
import glob
import pyarrow.dataset as ds
import pyarrow.parquet as pq

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))

yearly_dir = os.path.join(project_root, "output", "yearly")
output_file = os.path.join(project_root, "output", "final_hourly_pcse_dataset_multiyear.parquet")

files = sorted(glob.glob(os.path.join(yearly_dir, "pcse_*.parquet")))

if not files:
    print(f"No yearly parquet files found in '{yearly_dir}'.")
    sys.exit(1)

print(f"Found {len(files)} yearly files. Merging...")

dataset = ds.dataset(files, format="parquet")
print("Writing merged file (streaming, low memory)...")

writer = None
total_rows = 0
for batch in dataset.to_batches(batch_size=100_000):
    if writer is None:
        writer = pq.ParquetWriter(output_file, batch.schema)
    writer.write_batch(batch)
    total_rows += len(batch)

if writer:
    writer.close()

size_gb = os.path.getsize(output_file) / (1024 ** 3)
print(f"Done. {total_rows:,} rows — {size_gb:.2f} GB")
