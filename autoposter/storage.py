"""Private durable state and a process-wide single-host worker lock."""
from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "autopilot.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents
                    (name TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS events
                    (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts
                    (post_id TEXT NOT NULL, revision INTEGER NOT NULL, platform TEXT NOT NULL,
                     token TEXT NOT NULL, created_at TEXT NOT NULL,
                     PRIMARY KEY (post_id, revision, platform));
                CREATE TABLE IF NOT EXISTS jobs
                    (id TEXT PRIMARY KEY, action TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def read(self, name, default, legacy_path=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM documents WHERE name=?", (name,)).fetchone()
            if row:
                return json.loads(row[0])
            value = copy.deepcopy(default)
            if legacy_path and Path(legacy_path).exists():
                # Corruption must stop the worker; never silently lose its publication history.
                value = json.loads(Path(legacy_path).read_text(encoding="utf-8-sig"))
                if not isinstance(value, type(default)):
                    raise ValueError(f"Invalid legacy state: {name}")
                if name == "posts":
                    for entry in value.values():
                        if entry.get("status") in ("pending", "approved", "publishing"):
                            entry.update(status="needs_review", approved_at=None, approval_hash=None,
                                         preview_delivered=False)
            db.execute("INSERT INTO documents VALUES (?, ?, 1)", (name, json.dumps(value, ensure_ascii=False)))
            self._event(db, "import", {"document": name})
            return value

    def write(self, name, value):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload FROM documents WHERE name=?", (name,)).fetchone()
            if previous and previous[0] == payload:
                return
            db.execute("""INSERT INTO documents VALUES (?, ?, 1)
                ON CONFLICT(name) DO UPDATE SET payload=excluded.payload, version=version+1""", (name, payload))
            self._event(db, "save", {"document": name})

    @staticmethod
    def _event(db, kind, detail):
        db.execute("INSERT INTO events(created_at,kind,detail) VALUES (?,?,?)",
                   (utcnow(), kind, json.dumps(detail, ensure_ascii=False)))

    def event(self, kind, detail):
        with self.connect() as db:
            self._event(db, kind, detail)

    def reserve(self, posts, entry, platform):
        """Compare persisted authorization and atomically record a unique attempt."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM documents WHERE name='posts'").fetchone()
            saved = json.loads(row[0]).get(entry["date"]) if row else None
            if not saved or saved.get("approval_hash") != entry.get("approval_hash"):
                raise RuntimeError("Persisted approval changed; reload state")
            if saved.get("revision") != entry["revision"] or saved.get(platform, {}).get("status") != "not_started":
                raise RuntimeError("Platform already reserved or revision changed")
            token = uuid.uuid4().hex
            try:
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?)",
                           (entry["date"], entry["revision"], platform, token, utcnow()))
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("Platform attempt already exists; reconcile before retry") from exc
            entry[platform].update(status="attempting", attempt_token=token, attempted_at=utcnow())
            db.execute("UPDATE documents SET payload=?, version=version+1 WHERE name='posts'",
                       (json.dumps(posts, ensure_ascii=False),))
            self._event(db, "reserve", {"post": entry["date"], "revision": entry["revision"], "platform": platform})

    def backup(self, destination):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError("Choose a new backup file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        from contextlib import closing
        with self.connect() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
        destination.chmod(0o600)

    def enqueue(self, action):
        if action not in ("auto", "draft_now", "publish_now"):
            raise ValueError("Invalid queued action")
        token = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (token, action, "queued", utcnow()))
        return token

    def interrupt_jobs(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")

    def take_job(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,action FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (row[0],))
                return {"id": row[0], "action": row[1]}

    def finish_job(self, token, status):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=? WHERE id=?", (status, token))


_local = threading.local()
_thread_lock = threading.RLock()


@contextmanager
def worker_lock(directory):
    """Reentrant inside one worker; exclusive across threads and processes."""
    with _thread_lock:
        if getattr(_local, "depth", 0):
            _local.depth += 1
            try:
                yield
            finally:
                _local.depth -= 1
            return
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "worker.lock").open("a+b") as handle:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                if handle.read(1) == b"":
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _local.depth = 1
            try:
                yield
            finally:
                _local.depth = 0
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)
