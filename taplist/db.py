"""SQLite storage for beers, taps and tap history."""

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS beers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    brewery     TEXT NOT NULL DEFAULT '',
    style       TEXT NOT NULL DEFAULT '',
    abv         REAL,
    ibu         REAL,
    description TEXT NOT NULL DEFAULT '',
    label_file  TEXT,
    label_url   TEXT,
    brewery_logo_file TEXT,
    brewery_logo_url  TEXT,
    display_art TEXT NOT NULL DEFAULT 'label',
    source      TEXT NOT NULL DEFAULT 'manual',
    source_id   TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS taps (
    number    INTEGER PRIMARY KEY,
    beer_id   INTEGER REFERENCES beers(id) ON DELETE SET NULL,
    tapped_at REAL
);
CREATE TABLE IF NOT EXISTS tap_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tap_number  INTEGER NOT NULL,
    beer_id     INTEGER NOT NULL REFERENCES beers(id) ON DELETE CASCADE,
    tapped_at   REAL NOT NULL,
    untapped_at REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_beer ON tap_history(beer_id);
CREATE INDEX IF NOT EXISTS idx_beers_source ON beers(source, source_id);
"""

BEER_FIELDS = ("name", "brewery", "style", "abv", "ibu", "description", "label_url", "brewery_logo_url",
               "display_art", "source", "source_id")
DISPLAY_ART = ("label", "brewery", "both")

# Columns added after the first release; applied to older databases on startup.
MIGRATIONS = (
    ("beers", "brewery_logo_file", "TEXT"),
    ("beers", "brewery_logo_url", "TEXT"),
    ("beers", "display_art", "TEXT NOT NULL DEFAULT 'label'"),
)


class Database:
    def __init__(self, path, tap_count=3):
        self.path = path
        self.tap_count = tap_count
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            for n in range(1, tap_count + 1):
                self._conn.execute("INSERT OR IGNORE INTO taps(number) VALUES (?)", (n,))
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '1')")
            self._conn.commit()

    # -- helpers -----------------------------------------------------------

    def _migrate(self):
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self._conn.commit()

    def _bump(self):
        self._conn.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'version'")

    def version(self):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
            return int(row["value"])

    @staticmethod
    def _beer_dict(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "brewery": row["brewery"],
            "style": row["style"],
            "abv": row["abv"],
            "ibu": row["ibu"],
            "description": row["description"],
            "label_file": row["label_file"],
            "label_url": row["label_url"],
            "brewery_logo_file": row["brewery_logo_file"],
            "brewery_logo_url": row["brewery_logo_url"],
            "display_art": row["display_art"] or "label",
            "source": row["source"],
            "source_id": row["source_id"],
            "created_at": row["created_at"],
        }

    # -- beers -------------------------------------------------------------

    def get_beer(self, beer_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM beers WHERE id = ?", (beer_id,)).fetchone()
            return self._beer_dict(row)

    def find_by_source(self, source, source_id):
        if not source or not source_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM beers WHERE source = ? AND source_id = ?", (source, str(source_id))
            ).fetchone()
            return self._beer_dict(row)

    def create_beer(self, data):
        clean = _clean_beer(data)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO beers(name, brewery, style, abv, ibu, description, label_url, brewery_logo_url,"
                " display_art, source, source_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    clean["name"], clean["brewery"], clean["style"], clean["abv"], clean["ibu"],
                    clean["description"], clean["label_url"], clean["brewery_logo_url"], clean["display_art"],
                    clean["source"], clean["source_id"], time.time(),
                ),
            )
            self._bump()
            self._conn.commit()
            return self.get_beer(cur.lastrowid)

    def update_beer(self, beer_id, data):
        clean = _clean_beer(data, partial=True)
        if not clean:
            return self.get_beer(beer_id)
        sets = ", ".join(f"{k} = ?" for k in clean)
        with self._lock:
            self._conn.execute(f"UPDATE beers SET {sets} WHERE id = ?", (*clean.values(), beer_id))
            self._bump()
            self._conn.commit()
            return self.get_beer(beer_id)

    def set_label_file(self, beer_id, filename, kind="label"):
        column = "brewery_logo_file" if kind == "brewery" else "label_file"
        with self._lock:
            self._conn.execute(f"UPDATE beers SET {column} = ? WHERE id = ?", (filename, beer_id))
            self._bump()
            self._conn.commit()

    def delete_beer(self, beer_id):
        with self._lock:
            beer = self.get_beer(beer_id)
            if beer is None:
                return None
            self._conn.execute("DELETE FROM beers WHERE id = ?", (beer_id,))
            self._bump()
            self._conn.commit()
            return beer

    def search_library(self, query, limit=10):
        q = f"%{query.strip()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM beers WHERE name LIKE ? OR brewery LIKE ? OR style LIKE ?"
                " ORDER BY created_at DESC LIMIT ?",
                (q, q, q, limit),
            ).fetchall()
            return [self._beer_dict(r) for r in rows]

    # -- taps --------------------------------------------------------------

    def taps(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.number, t.tapped_at, b.* FROM taps t LEFT JOIN beers b ON b.id = t.beer_id"
                " ORDER BY t.number"
            ).fetchall()
            out = []
            for r in rows:
                beer = self._beer_dict(r) if r["id"] is not None else None
                out.append({"number": r["number"], "tapped_at": r["tapped_at"], "beer": beer})
            return out

    def on_tap_ids(self):
        with self._lock:
            rows = self._conn.execute("SELECT beer_id FROM taps WHERE beer_id IS NOT NULL").fetchall()
            return {r["beer_id"] for r in rows}

    def set_tap(self, number, beer_id):
        """Put beer on tap `number`, archiving whatever was there. beer_id=None clears the tap."""
        now = time.time()
        with self._lock:
            tap = self._conn.execute("SELECT beer_id FROM taps WHERE number = ?", (number,)).fetchone()
            if tap is None:
                raise KeyError(number)
            if tap["beer_id"] == beer_id and beer_id is not None:
                return  # no-op
            self._conn.execute(
                "UPDATE tap_history SET untapped_at = ? WHERE tap_number = ? AND untapped_at IS NULL",
                (now, number),
            )
            self._conn.execute(
                "UPDATE taps SET beer_id = ?, tapped_at = ? WHERE number = ?",
                (beer_id, now if beer_id else None, number),
            )
            if beer_id is not None:
                self._conn.execute(
                    "INSERT INTO tap_history(tap_number, beer_id, tapped_at) VALUES (?,?,?)",
                    (number, beer_id, now),
                )
            self._bump()
            self._conn.commit()

    # -- archive -----------------------------------------------------------

    def archive(self):
        """Every beer not currently on tap, with its pour history."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT b.*,
                       COUNT(h.id)                         AS times_tapped,
                       MAX(COALESCE(h.untapped_at, 0))     AS last_untapped,
                       MAX(h.tapped_at)                    AS last_tapped,
                       SUM(COALESCE(h.untapped_at, ?) - h.tapped_at) AS seconds_on_tap
                FROM beers b
                LEFT JOIN tap_history h ON h.beer_id = b.id
                WHERE b.id NOT IN (SELECT beer_id FROM taps WHERE beer_id IS NOT NULL)
                GROUP BY b.id
                ORDER BY COALESCE(MAX(h.untapped_at), b.created_at) DESC
                """,
                (time.time(),),
            ).fetchall()
            out = []
            for r in rows:
                beer = self._beer_dict(r)
                beer.update(
                    {
                        "times_tapped": r["times_tapped"],
                        "last_tapped": r["last_tapped"],
                        "last_untapped": r["last_untapped"] or None,
                        "seconds_on_tap": r["seconds_on_tap"] or 0,
                    }
                )
                out.append(beer)
            return out

    def history(self, limit=200):
        with self._lock:
            rows = self._conn.execute(
                "SELECT h.*, b.name, b.brewery FROM tap_history h JOIN beers b ON b.id = h.beer_id"
                " ORDER BY h.tapped_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def close(self):
        self._conn.close()


def _num(value):
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_beer(data, partial=False):
    out = {}
    for key in BEER_FIELDS:
        if partial and key not in data:
            continue
        val = data.get(key)
        if key in ("abv", "ibu"):
            out[key] = _num(val)
        elif key == "source_id":
            out[key] = str(val) if val not in (None, "") else None
        elif key in ("label_url", "brewery_logo_url"):
            out[key] = (val or "").strip() or None
        elif key == "display_art":
            val = (val or "label").strip().lower()
            if val not in DISPLAY_ART:
                raise ValueError("display_art must be one of: " + ", ".join(DISPLAY_ART))
            out[key] = val
        elif key == "source":
            out[key] = (val or "manual").strip() or "manual"
        else:
            out[key] = (val or "").strip()
    if not partial and not out.get("name"):
        raise ValueError("name is required")
    if partial and "name" in out and not out["name"]:
        raise ValueError("name cannot be empty")
    return out
