import json
import sqlite3
import time

from ..normalization import clean_event, event_key


class Outbox:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            key TEXT PRIMARY KEY, device TEXT NOT NULL, payload TEXT NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '')""")
        self.db.commit()

    def put(self, device, events):
        for event in events:
            event = clean_event(event)
            self.db.execute(
                "INSERT OR IGNORE INTO events(key,device,payload) VALUES(?,?,?)",
                (event_key(device, event), device, json.dumps(event)),
            )
        self.db.commit()

    def batch(self, device, limit=200, keys=None):
        query = "SELECT key,payload FROM events WHERE device=? AND delivered=0 AND next_attempt<=?"
        params = [device, time.time()]
        if keys is not None:
            if not keys:
                return []
            if len(keys) > 200:
                raise ValueError("Select at most 200 event keys")
            query += " AND key IN (" + ",".join("?" for _ in keys) + ")"
            params.extend(keys)
        query += " ORDER BY rowid LIMIT ?"
        params.append(limit)
        rows = self.db.execute(query, params).fetchall()
        return [(key, json.loads(payload)) for key, payload in rows]

    def acknowledge(self, batch, acknowledgements):
        # Per-event, key-verified acknowledgements only. Missing/rejected rows remain durable.
        acknowledged = set()
        for ack in acknowledgements:
            index = ack.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(batch):
                continue
            key = batch[index][0]
            if ack.get("accepted") is True and ack.get("key") == key:
                self.db.execute("UPDATE events SET delivered=1,last_error='' WHERE key=?", (key,))
                acknowledged.add(key)
        for key, _ in batch:
            if key not in acknowledged:
                self.failure([key], "Event not acknowledged; inspect server validation")
        self.db.commit()

    def failure(self, keys, reason="Transfer unavailable"):
        for key in keys:
            row = self.db.execute("SELECT attempts FROM events WHERE key=?", (key,)).fetchone()
            if row:
                attempts = row[0] + 1
                delay = min(3600, 5 * 2 ** min(attempts, 10))
                self.db.execute(
                    "UPDATE events SET attempts=?,next_attempt=?,last_error=? WHERE key=?",
                    (attempts, time.time() + delay, reason[:200], key),
                )
        self.db.commit()

    def status(self):
        return dict(
            self.db.execute(
                "SELECT CASE WHEN delivered=1 THEN 'delivered' ELSE 'pending' END, "
                "COUNT(*) FROM events GROUP BY delivered"
            ).fetchall()
        )

    def pending_count(self, device):
        return self.db.execute(
            "SELECT COUNT(*) FROM events WHERE device=? AND delivered=0", (device,)
        ).fetchone()[0]

    def close(self):
        self.db.close()
