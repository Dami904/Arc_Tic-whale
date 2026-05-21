import os
import sqlite3
from datetime import datetime
from backend.logger import get_logger

log = get_logger("database")
DB_NAME = os.path.join(os.path.dirname(__file__), "agora_marketplace.db")


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # ── Copy-traders ────────────────────────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS followers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_wallet_id TEXT NOT NULL,
            target_agent TEXT NOT NULL,
            allocation_amount REAL NOT NULL,
            asset TEXT,
            is_active INTEGER DEFAULT 1
        )
    ''')
    cursor.execute("PRAGMA table_info(followers)")
    follower_columns = {row[1] for row in cursor.fetchall()}
    if "asset" not in follower_columns:
        cursor.execute("ALTER TABLE followers ADD COLUMN asset TEXT")

    # ── Trade history ────────────────────────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            asset TEXT,
            tx_id TEXT,
            reason TEXT,
            timestamp TEXT NOT NULL
        )
    ''')

    # ── Kill-switch / settings ───────────────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    # Seed defaults (only if not already present)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('kill_switch', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('trade_alerts', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_summary', '1')")

    conn.commit()
    conn.close()
    log.info("Database initialised successfully.")


# ── Followers ────────────────────────────────────────────────────────────────

def add_follower(user_id, user_wallet_id, target_agent, allocation_amount, asset=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO followers (user_id, user_wallet_id, target_agent, allocation_amount, asset)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, user_wallet_id, target_agent, allocation_amount, asset))
    conn.commit()
    conn.close()
    log.info("User %s is now following %s.", user_id, target_agent)


def get_active_followers(agent_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_wallet_id, allocation_amount FROM followers
        WHERE target_agent = ? AND is_active = 1
    ''', (agent_name,))
    followers = cursor.fetchall()
    conn.close()
    return followers


def get_follower_wallet(agent_name, user_id):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, user_wallet_id, target_agent, allocation_amount, asset, is_active
        FROM followers
        WHERE target_agent = ? AND user_id = ? AND is_active = 1
        ORDER BY id DESC
        LIMIT 1
    ''', (agent_name, user_id))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_follower_summary(agent_name):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            COUNT(*) AS total_followers,
            COALESCE(SUM(allocation_amount), 0) AS total_allocation
        FROM followers
        WHERE target_agent = ? AND is_active = 1
    ''', (agent_name,))
    summary = dict(cursor.fetchone())
    cursor.execute('''
        SELECT COALESCE(asset, 'UNASSIGNED') AS asset, COALESCE(SUM(allocation_amount), 0) AS amount
        FROM followers
        WHERE target_agent = ? AND is_active = 1
        GROUP BY COALESCE(asset, 'UNASSIGNED')
        ORDER BY amount DESC
    ''', (agent_name,))
    summary["by_asset"] = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return summary


# ── Trade history ─────────────────────────────────────────────────────────────

def log_trade(agent: str, action: str, asset: str | None, tx_id: str | None, reason: str = ""):
    """Persist a completed trade (or HOLD decision) to the history table."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trade_history (agent, action, asset, tx_id, reason, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (agent, action, asset, tx_id, reason, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_trade_history(limit: int = 50) -> list[dict]:
    """Return the most recent trades as a list of dicts, newest first."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, agent, action, asset, tx_id, reason, timestamp
        FROM trade_history
        ORDER BY id DESC
        LIMIT ?
    ''', (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_trade_metrics(agent: str) -> dict:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT action, tx_id FROM trade_history WHERE agent = ?", (agent,))
    rows = cursor.fetchall()
    conn.close()

    executable = [row for row in rows if row[0] in {"BUY", "SELL"}]
    successful = [row for row in executable if row[1]]
    holds = [row for row in rows if row[0] == "HOLD"]
    win_rate = round((len(successful) / len(executable)) * 100, 1) if executable else 0.0
    return {
        "total_decisions": len(rows),
        "total_trades": len(executable),
        "successful_trades": len(successful),
        "holds": len(holds),
        "win_rate": win_rate,
    }


# ── Settings / Kill-switch ────────────────────────────────────────────────────

def get_setting(key: str, default: str = "0") -> str:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def is_kill_switch_active() -> bool:
    return get_setting("kill_switch", "0") == "1"


if __name__ == "__main__":
    init_db()
