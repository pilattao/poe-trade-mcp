"""Local PoE2 price observations, isolated by exact league/category/item variant.

No upstream price_db.py is present in this repository. This cache is populated
only by successful public economy fetches. It never reads legacy price_history.db.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3


class PriceCache:
    def __init__(self, path=None):
        self.path = Path(
            path
            or os.environ.get("POE_PRICE_DB")
            or Path(__file__).parent / ".cache" / "poe2-prices.sqlite3"
        )

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, league TEXT NOT NULL, category TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, digest TEXT NOT NULL,
                    UNIQUE(league,category,fetched_at));
                CREATE TABLE IF NOT EXISTS prices (
                    snapshot INTEGER REFERENCES snapshots(id), identity TEXT NOT NULL,
                    name TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(snapshot,identity));
                CREATE INDEX IF NOT EXISTS price_names ON prices(name);
            """)
            with db:
                yield db
        finally:
            db.close()

    def record(self, league, category, rows, fetched_at=None):
        if not rows:
            return 0
        fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(rows, sort_keys=True, allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO snapshots(league,category,fetched_at,digest) VALUES(?,?,?,?)",
                (league, category, fetched_at, digest),
            )
            if not cursor.rowcount:
                return 0
            snapshot = cursor.lastrowid
            for row in rows:
                identity = json.dumps(
                    [
                        str(row["id"]),
                        row.get("variant"),
                        row.get("base_type"),
                        row.get("corrupted"),
                    ]
                )
                db.execute(
                    "INSERT INTO prices VALUES(?,?,?,?)",
                    (snapshot, identity, row["name"], json.dumps(row, allow_nan=False)),
                )
        return len(rows)

    def _rows(self, league, latest=False):
        with self.connect() as db:
            clause = ""
            if latest:
                clause = """AND s.id = (SELECT s2.id FROM snapshots s2 WHERE
                    s2.league=s.league AND s2.category=s.category ORDER BY s2.fetched_at DESC,s2.id DESC LIMIT 1)"""
            rows = db.execute(
                f"""SELECT p.data,p.identity,s.fetched_at,s.league,s.category FROM prices p JOIN snapshots s ON s.id=p.snapshot
                WHERE s.league=? {clause} ORDER BY s.fetched_at,s.id""",
                (league,),
            ).fetchall()
            return [
                {
                    **json.loads(r["data"]),
                    "league": r["league"],
                    "category": r["category"],
                    "fetched_at": r["fetched_at"],
                    "_identity": r["identity"],
                }
                for r in rows
            ]

    def search(self, league, query, limit=20):
        return [
            self._public(r)
            for r in self._rows(league, True)
            if query.casefold() in r["name"].casefold()
        ][:limit]

    def history(self, league, name):
        return [
            self._public(r)
            for r in self._rows(league)
            if r["name"].casefold() == name.casefold()
        ]

    @staticmethod
    def _public(row):
        return {k: v for k, v in row.items() if k != "_identity"}

    def movers(self, league, min_snapshots=3, limit=25, direction="both", min_price=0):
        groups = {}
        for row in self._rows(league):
            groups.setdefault(
                (row["category"], row["_identity"], row["currency"]), []
            ).append(row)
        latest = {
            (r["category"], r["_identity"], r["currency"])
            for r in self._rows(league, True)
        }
        out = []
        for key, rows in groups.items():
            first, last = rows[0], rows[-1]
            if (
                key not in latest
                or len(rows) < min_snapshots
                or first["price"] <= 0
                or last["price"] < min_price
            ):
                continue
            change = (last["price"] / first["price"] - 1) * 100
            if (direction == "up" and change <= 0) or (
                direction == "down" and change >= 0
            ):
                continue
            out.append(
                {
                    **self._public(last),
                    "first_price": first["price"],
                    "last_price": last["price"],
                    "change_pct": change,
                    "snapshots": len(rows),
                }
            )
        return sorted(out, key=lambda r: abs(r["change_pct"]), reverse=True)[:limit]

    def status(self, league):
        with self.connect() as db:
            row = db.execute(
                "SELECT count(*) n,min(fetched_at) oldest,max(fetched_at) latest FROM snapshots WHERE league=?",
                (league,),
            ).fetchone()
        return {
            "game": "poe2",
            "league": league,
            "total_snapshots": row["n"],
            "oldest_fetch": row["oldest"],
            "latest_fetch": row["latest"],
            "items_tracked": len(self._rows(league, True)),
            "note": "Only successful local observations; no historical backfill or background polling.",
        }
