"""
app.py — Sample Python Flask app
Runs in Docker on EC2, connects to PostgreSQL on host, ships logs to CloudWatch.
"""

import os
import logging
import json
import time
from collections import deque
from datetime import datetime

from flask import Flask, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor

# ── Logging setup ───────────────────────────────────────────────────────────
# Logs written to /var/log/app/app.log → picked up by CloudWatch Agent
os.makedirs("/var/log/app", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),                              # Docker stdout → CloudWatch docker group
        logging.FileHandler("/var/log/app/app.log"),         # File → CloudWatch app group
    ],
)
logger = logging.getLogger("flask-app")

# ── App ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Database config from environment variables ───────────────────────────────
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "host.docker.internal"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "appdb"),
    "user":     os.environ.get("DB_USER", "appuser"),
    "password": os.environ.get("DB_PASS", "changeme"),
}

ORIGINAL_DB_HOST = DB_CONFIG["host"]
REQUIRED_TABLES = ("users", "events")
QUERY_AUDIT = deque(maxlen=500)


def get_db_connection():
    """Return a new psycopg2 connection."""
    cfg = dict(DB_CONFIG)
    cfg.setdefault("connect_timeout", 5)
    return psycopg2.connect(**cfg)


def execute_sql(cur, query, params=None, context="sql"):
    """Execute SQL while recording a structured audit trail and timings."""
    started = time.perf_counter()
    audit_item = {
        "time": datetime.utcnow().isoformat() + "Z",
        "context": context,
        "query": " ".join(query.split()),
        "ok": False,
    }
    try:
        if params is None:
            cur.execute(query)
        else:
            cur.execute(query, params)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        audit_item["ok"] = True
        audit_item["duration_ms"] = elapsed_ms
        QUERY_AUDIT.append(audit_item)
        logger.info("SQL OK [%s] %.3fms %s", context, elapsed_ms, audit_item["query"])
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        audit_item["duration_ms"] = elapsed_ms
        audit_item["error"] = str(exc)
        QUERY_AUDIT.append(audit_item)
        logger.error("SQL FAIL [%s] %.3fms %s -- %s", context, elapsed_ms, audit_item["query"], exc)
        raise


def init_db():
    """Create tables if they don't exist yet."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            execute_sql(cur, """
                CREATE TABLE IF NOT EXISTS users (
                    id         SERIAL PRIMARY KEY,
                    name       VARCHAR(120) NOT NULL,
                    email      VARCHAR(255) UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS events (
                    id         SERIAL PRIMARY KEY,
                    event_type VARCHAR(80) NOT NULL,
                    payload    JSONB,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """, context="init_db")
            conn.commit()
        conn.close()
        logger.info("Database tables initialised successfully")
    except Exception as exc:
        logger.error("Database init failed: %s", exc)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    logger.info("GET / — index called")
    return jsonify({
        "message": "Flask app running on EC2!",
        "status":  "ok",
        "time":    datetime.utcnow().isoformat(),
    })


@app.route("/health")
def health():
    """Health check — verifies DB connectivity."""
    try:
        conn = get_db_connection()
        conn.close()
        logger.info("Health check passed — DB connected")
        return jsonify({"status": "healthy", "database": "connected"})
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        return jsonify({"status": "unhealthy", "database": str(exc)}), 500


@app.route("/ops/db-status", methods=["GET"])
def db_status():
    """Deep DB verification: connectivity, required tables, and active queries."""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            execute_sql(
                cur,
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                ORDER BY table_name;
                """,
                (list(REQUIRED_TABLES),),
                context="ops.db_status.tables",
            )
            table_rows = cur.fetchall()
            execute_sql(
                cur,
                """
                SELECT pid, usename, datname, state, query
                FROM pg_stat_activity
                WHERE datname = current_database()
                ORDER BY query_start DESC NULLS LAST
                LIMIT 20;
                """,
                context="ops.db_status.activity",
            )
            activity_rows = cur.fetchall()
        conn.close()

        found = {r["table_name"] for r in table_rows}
        missing = [t for t in REQUIRED_TABLES if t not in found]
        status = "ok" if not missing else "degraded"
        return jsonify({
            "status": status,
            "db_host": DB_CONFIG["host"],
            "required_tables": list(REQUIRED_TABLES),
            "missing_tables": missing,
            "active_queries": [dict(r) for r in activity_rows],
            "recent_query_audit": list(QUERY_AUDIT)[-30:],
        }), 200 if status == "ok" else 503
    except Exception as exc:
        logger.error("ops/db-status blocked: %s", exc)
        return jsonify({
            "status": "blocked",
            "db_host": DB_CONFIG["host"],
            "error": str(exc),
            "recent_query_audit": list(QUERY_AUDIT)[-30:],
        }), 503


@app.route("/ops/query-audit", methods=["GET"])
def query_audit():
    """Return recent SQL query execution audit (application-level)."""
    return jsonify({"count": len(QUERY_AUDIT), "items": list(QUERY_AUDIT)})


@app.route("/ops/db-host", methods=["POST"])
def set_db_host():
    """Override DB host to simulate failures or recover after mitigation."""
    data = request.get_json(silent=True) or {}
    host = (data.get("host") or "").strip()
    if not host:
        return jsonify({"error": "host is required"}), 400
    DB_CONFIG["host"] = host
    logger.warning("DB host overridden by ops endpoint: %s", host)
    return jsonify({"message": "db host updated", "db_host": DB_CONFIG["host"]})


@app.route("/ops/db-host/reset", methods=["POST"])
def reset_db_host():
    """Reset DB host to original value from startup env."""
    DB_CONFIG["host"] = ORIGINAL_DB_HOST
    logger.info("DB host reset to original: %s", ORIGINAL_DB_HOST)
    return jsonify({"message": "db host reset", "db_host": DB_CONFIG["host"]})


# ── Users CRUD ───────────────────────────────────────────────────────────────

@app.route("/users", methods=["GET"])
def get_users():
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            execute_sql(cur, "SELECT id, name, email, created_at FROM users ORDER BY id;", context="users.list")
            rows = cur.fetchall()
        conn.close()
        logger.info("GET /users — returned %d rows", len(rows))
        return jsonify({"users": [dict(r) for r in rows], "count": len(rows)})
    except Exception as exc:
        logger.error("GET /users error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            execute_sql(cur, "SELECT * FROM users WHERE id = %s;", (user_id,), context="users.get")
            row = cur.fetchone()
        conn.close()
        if row is None:
            return jsonify({"error": "User not found"}), 404
        logger.info("GET /users/%d — found", user_id)
        return jsonify(dict(row))
    except Exception as exc:
        logger.error("GET /users/%d error: %s", user_id, exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/users", methods=["POST"])
def create_user():
    data = request.get_json(silent=True) or {}
    name  = data.get("name", "").strip()
    email = data.get("email", "").strip()

    if not name or not email:
        return jsonify({"error": "name and email are required"}), 400

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            execute_sql(
                cur,
                "INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id;",
                (name, email),
                context="users.create",
            )
            new_id = cur.fetchone()[0]
            conn.commit()
        conn.close()
        logger.info("POST /users — created user id=%d name=%s", new_id, name)

        # Also log an event
        _log_event("user_created", {"user_id": new_id, "name": name})

        return jsonify({"id": new_id, "message": "User created"}), 201
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "Email already exists"}), 409
    except Exception as exc:
        logger.error("POST /users error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            execute_sql(cur, "DELETE FROM users WHERE id = %s RETURNING id;", (user_id,), context="users.delete")
            deleted = cur.fetchone()
            conn.commit()
        conn.close()
        if deleted is None:
            return jsonify({"error": "User not found"}), 404
        logger.info("DELETE /users/%d — deleted", user_id)
        return jsonify({"message": f"User {user_id} deleted"})
    except Exception as exc:
        logger.error("DELETE /users/%d error: %s", user_id, exc)
        return jsonify({"error": str(exc)}), 500


# ── Events log ───────────────────────────────────────────────────────────────

@app.route("/events", methods=["GET"])
def get_events():
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            execute_sql(cur, "SELECT * FROM events ORDER BY created_at DESC LIMIT 50;", context="events.list")
            rows = cur.fetchall()
        conn.close()
        return jsonify({"events": [dict(r) for r in rows]})
    except Exception as exc:
        logger.error("GET /events error: %s", exc)
        return jsonify({"error": str(exc)}), 500


def _log_event(event_type: str, payload: dict):
    """Write an event row to the events table."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            execute_sql(
                cur,
                "INSERT INTO events (event_type, payload) VALUES (%s, %s);",
                (event_type, json.dumps(payload)),
                context="events.insert",
            )
            conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Could not log event: %s", exc)


# ── Entry point ──────────────────────────────────────────────────────────────

# Ensure tables are created when running under Gunicorn (module import path).
init_db()

if __name__ == "__main__":
    logger.info("Starting Flask app")
    app.run(host="0.0.0.0", port=8080, debug=False)
