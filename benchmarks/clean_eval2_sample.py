"""Draw the sample for docs/clean-eval-2-protocol.md from Toucan shards. Needs pyarrow (not a figured
dependency): python benchmarks/clean_eval2_sample.py, after downloading shard 7 of each config to
benchmarks/data/clean2/toucan/{Kimi-K2,Qwen3,OSS}.parquet."""

import json
import random
from pathlib import Path

import pyarrow.parquet as pq

DIR = Path(__file__).parent / "data" / "clean2" / "toucan"
for name in ("Kimi-K2", "Qwen3", "OSS"):
    rows = pq.read_table(DIR / f"{name}.parquet").to_pylist()
    eligible = sorted(i for i, r in enumerate(rows) if '"function_call"' in r["messages"])
    picked = random.Random(7).sample(eligible, 1000)
    sample = [rows[i] for i in sorted(picked)]
    (DIR / f"{name}.sample.json").write_text(json.dumps(sample))
    print(name, len(rows), "rows,", len(eligible), "with a function call, sampled", len(sample))
