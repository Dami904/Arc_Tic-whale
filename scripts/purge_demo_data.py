"""
purge_demo_data.py - One-time cleanup of seeded demo rows before public beta.
Deletes trade_history/social_posts/followers/users rows created by seed_demo.py.
Run: python scripts/purge_demo_data.py            (prints what it WOULD delete)
     python scripts/purge_demo_data.py --apply    (actually deletes)
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backend.database as db

APPLY = "--apply" in sys.argv

# Fixed predicates targeting only rows seed_demo.py creates - not user input.
DEMO_PREDICATES = [
    ("trade_history", "tx_id LIKE '0xabc%' OR tx_id LIKE '0xf0%' OR agent LIKE '%dryrun%' OR agent LIKE '%demo%'"),
    ("social_posts",  "tx_id LIKE '0xabc%' OR tx_id LIKE '0xf0%'"),
    ("followers",     "user_id IN ('dryrun_user') OR user_wallet_id IN ('wallet_dryrun_001', 'wallet_demo_001')"),
    ("users",         "user_id IN ('dryrun_user') OR wallet_id IN ('wallet_dryrun_001', 'wallet_demo_001')"),
]

with db._connection() as conn:
    cur = db._cursor(conn)
    for table, predicate in DEMO_PREDICATES:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {predicate}")
        n = db._row(cur)["n"]
        if APPLY:
            cur.execute(f"DELETE FROM {table} WHERE {predicate}")
            print(f"{table}: deleted {n} demo rows")
        else:
            print(f"{table}: WOULD delete {n} demo rows (run with --apply)")
    if APPLY:
        conn.commit()
