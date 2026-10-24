"""Stateless Flask API for Vercel.

The browser keeps the data in localStorage and sends it with every request. The server loads it
into a throwaway in-memory SQLite DB, runs the agent, and returns the updated data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request  # noqa: E402

import agent  # noqa: E402
import db  # noqa: E402

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
MAX_ROWS = 500
MAX_MESSAGE = 500


def _clean(data):
    data = data if isinstance(data, dict) else {}
    out = {}
    for t in db.SNAPSHOT_TABLES:
        rows = data.get(t, [])
        out[t] = [r for r in rows if isinstance(r, dict)][:MAX_ROWS] if isinstance(rows, list) else []
    return out


def _overview():
    return {
        "utilization": db.utilization(),
        "projects": db.query("SELECT * FROM projects ORDER BY name"),
        "allocations": db.allocation_rows(),
        "alerts": agent._alerts(),
    }


def _run(data, fn):
    db.open_memory(_clean(data))
    try:
        return fn(), db.snapshot()
    finally:
        db.close_memory()


@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", ""))[:MAX_MESSAGE]
    state = body.get("state") if isinstance(body.get("state"), dict) else {}

    def go():
        reply, suggestions = agent.respond(message, state)
        return {"reply": reply, "suggestions": suggestions, "state": state, "overview": _overview()}

    result, data = _run(body.get("data"), go)
    result["data"] = data
    return jsonify(result)


@app.post("/api/overview")
def overview():
    body = request.get_json(silent=True) or {}
    result, data = _run(body.get("data"), _overview)
    return jsonify({"overview": result, "data": data})


@app.get("/api/health")
def health():
    return jsonify({"ok": True})
