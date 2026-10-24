import sqlite3
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


def conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    return c


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
