import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "pmo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS resources(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT, skills TEXT,
  capacity_hours REAL DEFAULT 40, cost_rate REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, status TEXT DEFAULT 'Active',
  start_date TEXT, end_date TEXT, budget REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS allocations(
  id INTEGER PRIMARY KEY,
  resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  hours_per_week REAL NOT NULL, start_date TEXT, end_date TEXT);
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY, user TEXT, role TEXT, content TEXT,
  ts TEXT DEFAULT CURRENT_TIMESTAMP);
"""


_local = threading.local()
SNAPSHOT_TABLES = ("resources", "projects", "allocations")


def _setup(c):
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    return c


def conn():
    mem = getattr(_local, "mem", None)
    if mem is not None:
        return mem
    DB_PATH.parent.mkdir(exist_ok=True)
    return _setup(sqlite3.connect(DB_PATH))


def open_memory(snapshot=None):
    """Use a throwaway in-memory DB (serverless mode), optionally preloaded from a snapshot."""
    c = _setup(sqlite3.connect(":memory:", check_same_thread=False))
    for table in SNAPSHOT_TABLES:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
        for row in (snapshot or {}).get(table, []):
            keep = {k: v for k, v in row.items() if k in cols}
            if keep:
                c.execute(f"INSERT INTO {table}({','.join(keep)}) VALUES({','.join('?' * len(keep))})",
                          list(keep.values()))
    c.commit()
    _local.mem = c


def snapshot():
    return {t: query(f"SELECT * FROM {t} ORDER BY id") for t in SNAPSHOT_TABLES}


def close_memory():
    mem = getattr(_local, "mem", None)
    if mem is not None:
        mem.close()
    _local.mem = None


def query(sql, params=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with conn() as c:
        return c.execute(sql, params).lastrowid


def utilization():
    return query("""
        SELECT r.id, r.name, r.role, r.capacity_hours,
               COALESCE(SUM(a.hours_per_week),0) AS allocated,
               r.capacity_hours - COALESCE(SUM(a.hours_per_week),0) AS available
        FROM resources r LEFT JOIN allocations a ON a.resource_id=r.id
        GROUP BY r.id ORDER BY r.name""")


def allocation_rows():
    return query("""
        SELECT a.id, r.name AS resource, p.name AS project, a.hours_per_week,
               a.start_date, a.end_date
        FROM allocations a JOIN resources r ON r.id=a.resource_id
        JOIN projects p ON p.id=a.project_id ORDER BY p.name, r.name""")


def find(table, name):
    rows = query(f"SELECT * FROM {table} WHERE lower(name)=lower(?)", (name.strip(),))
    return rows[0] if rows else None
