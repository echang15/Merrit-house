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
    rating      INTEGER,
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
               "display_art", "source", "source_id", "rating")
DISPLAY_ART = ("label", "brewery", "both")
RATING_MIN, RATING_MAX = 1, 5

# Columns added after the first release; applied to older databases on startup.
MIGRATIONS = (
    ("beers", "brewery_logo_file", "TEXT"),
    ("beers", "brewery_logo_url", "TEXT"),
    ("beers", "display_art", "TEXT NOT NULL DEFAULT 'label'"),
    ("beers", "rating", "INTEGER"),
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
            "rating": row["rating"],
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
                " display_art, source, source_id, rating, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    clean["name"], clean["brewery"], clean["style"], clean["abv"], clean["ibu"],
                    clean["description"], clean["label_url"], clean["brewery_logo_url"], clean["display_art"],
                    clean["source"], clean["source_id"], clean["rating"], time.time(),
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

    # -- metrics -----------------------------------------------------------

    def metrics(self, months=12, top=8):
        """Aggregates over everything that has ever been on tap, for the stats page."""
        now = time.time()
        with self._lock:
            q = self._conn.execute
            totals = q(
                """
                SELECT COUNT(*)                                        AS kegs,
                       COUNT(DISTINCT beer_id)                         AS beers_poured,
                       SUM(COALESCE(untapped_at, ?) - tapped_at)       AS seconds,
                       MIN(tapped_at)                                  AS first_tapped,
                       SUM(CASE WHEN untapped_at IS NULL THEN 1 ELSE 0 END) AS pouring
                FROM tap_history
                """,
                (now,),
            ).fetchone()
            ratings = q(
                "SELECT COUNT(*) AS rated, AVG(rating) AS avg_rating FROM beers WHERE rating IS NOT NULL"
            ).fetchone()
            rating_counts = {n: 0 for n in range(RATING_MIN, RATING_MAX + 1)}
            for r in q("SELECT rating, COUNT(*) AS n FROM beers WHERE rating IS NOT NULL GROUP BY rating"):
                if r["rating"] in rating_counts:
                    rating_counts[r["rating"]] = r["n"]

            per_beer = """
                SELECT b.*,
                       COUNT(h.id)                                    AS times_tapped,
                       SUM(COALESCE(h.untapped_at, ?) - h.tapped_at)  AS seconds_on_tap,
                       MAX(h.tapped_at)                               AS last_tapped,
                       EXISTS(SELECT 1 FROM taps t WHERE t.beer_id = b.id) AS on_tap
                FROM beers b JOIN tap_history h ON h.beer_id = b.id
                GROUP BY b.id
            """

            def beers(order, where="", limit=top):
                rows = q(f"SELECT * FROM ({per_beer}) {where} ORDER BY {order} LIMIT ?", (now, limit)).fetchall()
                out = []
                for r in rows:
                    beer = self._beer_dict(r)
                    beer.update(
                        times_tapped=r["times_tapped"],
                        seconds_on_tap=r["seconds_on_tap"] or 0,
                        last_tapped=r["last_tapped"],
                        on_tap=bool(r["on_tap"]),
                    )
                    out.append(beer)
                return out

            top_rated = beers("rating DESC, times_tapped DESC, last_tapped DESC", "WHERE rating IS NOT NULL")
            most_tapped = beers("times_tapped DESC, seconds_on_tap DESC")
            longest = beers("seconds_on_tap DESC")

            def grouped(column):
                rows = q(
                    f"""
                    SELECT COALESCE(NULLIF(TRIM(b.{column}), ''), 'Unknown') AS label, COUNT(h.id) AS kegs
                    FROM tap_history h JOIN beers b ON b.id = h.beer_id
                    GROUP BY LOWER(TRIM(b.{column}))
                    ORDER BY kegs DESC, label
                    """
                ).fetchall()
                out = [{"label": r["label"], "kegs": r["kegs"]} for r in rows[:top]]
                rest = sum(r["kegs"] for r in rows[top:])
                if rest:
                    out.append({"label": "Other", "kegs": rest})
                return out

            # Kegs tapped per calendar month, oldest first, always `months` buckets.
            by_month = []
            year, month = time.localtime(now)[:2]
            keys = []
            for _ in range(months):
                keys.append(f"{year:04d}-{month:02d}")
                month -= 1
                if month == 0:
                    year, month = year - 1, 12
            keys.reverse()
            counts = {r["ym"]: r["n"] for r in q(
                "SELECT strftime('%Y-%m', tapped_at, 'unixepoch', 'localtime') AS ym, COUNT(*) AS n"
                " FROM tap_history GROUP BY ym"
            )}
            for key in keys:
                by_month.append({"month": key, "kegs": counts.get(key, 0)})

            recent = [dict(r) for r in q(
                "SELECT h.*, b.name, b.brewery, b.rating FROM tap_history h JOIN beers b ON b.id = h.beer_id"
                " ORDER BY h.tapped_at DESC LIMIT ?",
                (top,),
            )]

            kegs = totals["kegs"] or 0
            return {
                "now": now,
                "kegs": kegs,
                "beers_poured": totals["beers_poured"] or 0,
                "beers_library": q("SELECT COUNT(*) AS n FROM beers").fetchone()["n"],
                "pouring": totals["pouring"] or 0,
                "seconds_on_tap": totals["seconds"] or 0,
                "avg_seconds_per_keg": (totals["seconds"] or 0) / kegs if kegs else 0,
                "first_tapped": totals["first_tapped"],
                "rated": ratings["rated"] or 0,
                "avg_rating": ratings["avg_rating"],
                "rating_counts": rating_counts,
                "top_rated": top_rated,
                "most_tapped": most_tapped,
                "longest": longest,
                "styles": grouped("style"),
                "breweries": grouped("brewery"),
                "by_month": by_month,
                "recent": recent,
            }

    def close(self):
        self._conn.close()


def _num(value):
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rating(value):
    """A score from 1 to 5, or None to clear it."""
    if value in (None, "", "null", 0, "0"):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"rating must be a whole number from {RATING_MIN} to {RATING_MAX}")
    if num != int(num) or not RATING_MIN <= num <= RATING_MAX:
        raise ValueError(f"rating must be a whole number from {RATING_MIN} to {RATING_MAX}")
    return int(num)


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
        elif key == "rating":
            out[key] = _rating(val)
        else:
            out[key] = (val or "").strip()
    if not partial and not out.get("name"):
        raise ValueError("name is required")
    if partial and "name" in out and not out["name"]:
        raise ValueError("name cannot be empty")
    return out
