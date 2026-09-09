"""Minimal project builder for poking the SOURCE layer only."""
from __future__ import annotations
import shutil, textwrap, yaml
from pathlib import Path
import pandas as pd

BASE = Path("/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")

def make(name: str, source: dict, fresh: bool = True) -> Path:
    root = BASE / name
    if fresh and root.exists():
        shutil.rmtree(root)
    (root / "seed").mkdir(parents=True, exist_ok=True)
    spec = {
        "project": name,
        "store": "./data/q.duckdb",
        "stages": [{"id": "raw", "kind": "raw"}, {"id": "weights", "kind": "weights"}],
        "sources": [source],
        "steps": [],
    }
    (root / "qanat.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return root

def open_store(root: Path):
    from qanat.project import load
    from qanat.store import Store
    p, r = load(root)
    return p, r, Store(p.store_url(r))

def poll(root: Path, store=None, p=None, r=None):
    """Run every source once. Returns (result_list, store, p, r)."""
    from qanat.project import load
    from qanat.runner import run_source
    from qanat.store import Store
    close = False
    if store is None:
        p, r = load(root)
        store = Store(p.store_url(r))
        close = True
    res = [run_source(store, p, r, s) for s in p.sources]
    return res, store, p, r, close

def show(store, ref="raw.bars", n=50):
    if not store.exists(ref):
        print(f"  [table {ref} does not exist]")
        return
    info = store.table_info(ref)
    print(f"  rows={info.rows} cols={info.columns}")
    df = store.read(ref, limit=n)
    print(textwrap.indent(df.to_string(), "  "))

def events(store, n=10):
    for e in reversed(store.recent_events(n)):
        print(f"  [{e['level']}] {e['job_id']}: {e['message']}")

def runs(store, n=10):
    for x in reversed(store.recent_runs(n)):
        print(f"  run {x['job_id']} status={x['status']} rows={x['rows_out']} err={x['error']}")
