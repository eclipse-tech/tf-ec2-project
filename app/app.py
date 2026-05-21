"""
app.py — Sample Python Flask app
Runs in Docker on EC2, connects to PostgreSQL on host, ships logs to CloudWatch.
"""

import os
import logging
import json
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


def get_db_connection():
    """Return a new psycopg2 connection."""
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    """Create tables if they don't exist yet."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
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
            """)
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


# ── Users CRUD ───────────────────────────────────────────────────────────────

@app.route("/users", methods=["GET"])
def get_users():
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name, email, created_at FROM users ORDER BY id;")
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
            cur.execute("SELECT * FROM users WHERE id = %s;", (user_id,))
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
            cur.execute(
                "INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id;",
                (name, email),
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
            cur.execute("DELETE FROM users WHERE id = %s RETURNING id;", (user_id,))
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
            cur.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT 50;")
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
            cur.execute(
                "INSERT INTO events (event_type, payload) VALUES (%s, %s);",
                (event_type, json.dumps(payload)),
            )
            conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Could not log event: %s", exc)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Flask app — init DB")
    init_db()
    app.run(host="0.0.0.0", port=8080, debug=False)
