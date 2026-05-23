import os
import sqlite3
from datetime import datetime
from backend.logger import get_logger

log = get_logger("database")

try:
    from backend.config import DATABASE_URL
except ImportError:
    DATABASE_URL = os.getenv("DATABASE_URL", "")

_USE_PG = bool(DATABASE_URL)
_PH = "%s" if _USE_PG else "?"

if _USE_PG:
    import psycopg2
    import psycopg2.extras

DB_NAME = os.path.join(os.path.dirname(__file__), "agora_marketplace.db")


def _connect():
    if _USE_PG:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _cursor(conn):
    if _USE_PG:
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()


def _row(cursor) -> dict | None:
    row = cursor.fetchone()
    return dict(row) if row else None


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def init_db():
    conn = _connect()
    cursor = _cursor(conn)

    if _USE_PG:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS followers (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                user_wallet_id TEXT NOT NULL,
                user_wallet_address TEXT,
                target_agent TEXT NOT NULL,
                allocation_amount REAL NOT NULL,
                asset TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS asset TEXT")
        cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS user_wallet_address TEXT")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                wallet_id TEXT NOT NULL,
                wallet_address TEXT NOT NULL,
                referral_code TEXT NOT NULL UNIQUE,
                referred_by TEXT,
                created_at TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id SERIAL PRIMARY KEY,
                referrer_user_id TEXT NOT NULL,
                referred_user_id TEXT NOT NULL,
                profit_amount REAL NOT NULL,
                reward_amount REAL NOT NULL,
                tx_id TEXT,
                timestamp TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_history (
                id SERIAL PRIMARY KEY,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                asset TEXT,
                tx_id TEXT,
                reason TEXT,
                timestamp TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        cursor.execute("INSERT INTO settings (key, value) VALUES ('kill_switch', '0') ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO settings (key, value) VALUES ('trade_alerts', '1') ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO settings (key, value) VALUES ('daily_summary', '1') ON CONFLICT DO NOTHING")

    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS followers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_wallet_id TEXT NOT NULL,
                user_wallet_address TEXT,
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
        if "user_wallet_address" not in follower_columns:
            cursor.execute("ALTER TABLE followers ADD COLUMN user_wallet_address TEXT")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                wallet_id TEXT NOT NULL,
                wallet_address TEXT NOT NULL,
                referral_code TEXT NOT NULL UNIQUE,
                referred_by TEXT,
                created_at TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_user_id TEXT NOT NULL,
                referred_user_id TEXT NOT NULL,
                profit_amount REAL NOT NULL,
                reward_amount REAL NOT NULL,
                tx_id TEXT,
                timestamp TEXT NOT NULL
            )
        ''')

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

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('kill_switch', '0')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('trade_alerts', '1')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_summary', '1')")

    conn.commit()
    conn.close()
    log.info("Database initialised (%s).", "postgresql" if _USE_PG else "sqlite")


# ── Users ─────────────────────────────────────────────────────────────────────

def get_user(user_id):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at FROM users WHERE user_id = {_PH}",
        (user_id,),
    )
    row = _row(cursor)
    conn.close()
    return row


def get_user_by_referral_code(referral_code):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at FROM users WHERE referral_code = {_PH}",
        (referral_code,),
    )
    row = _row(cursor)
    conn.close()
    return row


def upsert_user_wallet(user_id, wallet_id, wallet_address, referral_code, referred_by=None):
    existing = get_user(user_id)
    if existing:
        return existing

    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO users (user_id, wallet_id, wallet_address, referral_code, referred_by, created_at) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (user_id, wallet_id, wallet_address, referral_code, referred_by, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return get_user(user_id)


# ── Referrals ─────────────────────────────────────────────────────────────────

def add_referral_reward(referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id=None):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO referral_rewards (referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id, timestamp) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_referral_summary(user_id):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(f"SELECT COUNT(*) AS total_referred FROM users WHERE referred_by = {_PH}", (user_id,))
    summary = _row(cursor)
    cursor.execute(
        f"SELECT COALESCE(SUM(reward_amount), 0) AS total_rewards FROM referral_rewards WHERE referrer_user_id = {_PH}",
        (user_id,),
    )
    summary.update(_row(cursor))
    cursor.execute(
        f"SELECT id, referred_user_id, profit_amount, reward_amount, tx_id, timestamp FROM referral_rewards WHERE referrer_user_id = {_PH} ORDER BY id DESC LIMIT 20",
        (user_id,),
    )
    summary["rewards"] = _rows(cursor)
    conn.close()
    return summary


# ── Followers ─────────────────────────────────────────────────────────────────

def add_follower(user_id, user_wallet_id, target_agent, allocation_amount, asset=None, user_wallet_address=None):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO followers (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset),
    )
    conn.commit()
    conn.close()
    log.info("User %s is now following %s.", user_id, target_agent)


def get_active_followers(agent_name):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_wallet_id, user_wallet_address, allocation_amount FROM followers WHERE target_agent = {_PH} AND is_active = 1",
        (agent_name,),
    )
    rows = _rows(cursor)
    conn.close()
    return [(r["user_wallet_id"], r["user_wallet_address"], r["allocation_amount"]) for r in rows]


def get_follower_wallet(agent_name, user_id):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, is_active FROM followers WHERE target_agent = {_PH} AND user_id = {_PH} AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (agent_name, user_id),
    )
    row = _row(cursor)
    conn.close()
    return row


def get_follower_by_wallet_id(agent_name, wallet_id):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, is_active FROM followers WHERE target_agent = {_PH} AND user_wallet_id = {_PH} AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (agent_name, wallet_id),
    )
    row = _row(cursor)
    conn.close()
    return row


def get_follower_summary(agent_name):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT COUNT(*) AS total_followers, COALESCE(SUM(allocation_amount), 0) AS total_allocation FROM followers WHERE target_agent = {_PH} AND is_active = 1",
        (agent_name,),
    )
    summary = _row(cursor)
    cursor.execute(
        f"SELECT COALESCE(asset, 'UNASSIGNED') AS asset, COALESCE(SUM(allocation_amount), 0) AS amount FROM followers WHERE target_agent = {_PH} AND is_active = 1 GROUP BY COALESCE(asset, 'UNASSIGNED') ORDER BY amount DESC",
        (agent_name,),
    )
    summary["by_asset"] = _rows(cursor)
    conn.close()
    return summary


# ── Trade history ─────────────────────────────────────────────────────────────

def log_trade(agent: str, action: str, asset: str | None, tx_id: str | None, reason: str = ""):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO trade_history (agent, action, asset, tx_id, reason, timestamp) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (agent, action, asset, tx_id, reason, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_trade_history(limit: int = 50, agent: str | None = None, actions: set[str] | None = None) -> list[dict]:
    conn = _connect()
    cursor = _cursor(conn)
    params: list = []
    where: list[str] = []
    if agent:
        where.append(f"agent = {_PH}")
        params.append(agent)
    if actions:
        placeholders = ",".join(_PH for _ in actions)
        where.append(f"action IN ({placeholders})")
        params.extend(sorted(actions))

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    cursor.execute(
        f"SELECT id, agent, action, asset, tx_id, reason, timestamp FROM trade_history {where_clause} ORDER BY id DESC LIMIT {_PH}",
        params,
    )
    rows = _rows(cursor)
    conn.close()
    return rows


def get_trade_metrics(agent: str) -> dict:
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(f"SELECT action, tx_id FROM trade_history WHERE agent = {_PH}", (agent,))
    rows = _rows(cursor)
    conn.close()

    executable = [r for r in rows if r["action"] in {"BUY", "SELL"}]
    successful = [r for r in executable if r["tx_id"]]
    holds = [r for r in rows if r["action"] == "HOLD"]
    win_rate = round((len(successful) / len(executable)) * 100, 1) if executable else 0.0
    return {
        "total_decisions": len(rows),
        "total_trades": len(executable),
        "successful_trades": len(successful),
        "holds": len(holds),
        "win_rate": win_rate,
    }


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "0") -> str:
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(f"SELECT value FROM settings WHERE key = {_PH}", (key,))
    row = _row(cursor)
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = _connect()
    cursor = _cursor(conn)
    if _USE_PG:
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
    else:
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def is_kill_switch_active() -> bool:
    return get_setting("kill_switch", "0") == "1"


if __name__ == "__main__":
    init_db()
