import os
import sqlite3
from datetime import datetime, timezone
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


def _select_follower_rows(cursor, base_sql: str, params: tuple, include_stop_loss: bool = True):
    try:
        cursor.execute(base_sql, params)
        return _rows(cursor)
    except Exception as exc:
        if include_stop_loss and "stop_loss_pct" in str(exc):
            fallback_sql = base_sql.replace(", stop_loss_pct", "")
            fallback_sql = fallback_sql.replace(", stop_loss_pct,", ",")
            fallback_sql = fallback_sql.replace("stop_loss_pct, ", "")
            fallback_sql = fallback_sql.replace(", stop_loss_pct ", " ")
            cursor.execute(fallback_sql, params)
            rows = _rows(cursor)
            for row in rows:
                row.setdefault("stop_loss_pct", 10.0)
            return rows
        raise


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
                stop_loss_pct REAL DEFAULT 10.0,
                is_active INTEGER DEFAULT 1
            )
        ''')
        cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS asset TEXT")
        cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS user_wallet_address TEXT")
        cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS stop_loss_pct REAL DEFAULT 10.0")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                wallet_id TEXT NOT NULL,
                wallet_address TEXT NOT NULL,
                referral_code TEXT NOT NULL UNIQUE,
                referred_by TEXT,
                created_at TEXT NOT NULL,
                email TEXT,
                telegram_chat_id TEXT,
                display_name TEXT,
                avatar_url TEXT,
                trade_alerts INTEGER DEFAULT 1,
                daily_summary INTEGER DEFAULT 1
            )
        ''')
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_chat_id TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trade_alerts INTEGER DEFAULT 1")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_summary INTEGER DEFAULT 1")

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
            CREATE TABLE IF NOT EXISTS social_posts (
                id SERIAL PRIMARY KEY,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                post_text TEXT NOT NULL,
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
                stop_loss_pct REAL DEFAULT 10.0,
                is_active INTEGER DEFAULT 1
            )
        ''')
        cursor.execute("PRAGMA table_info(followers)")
        follower_columns = {row[1] for row in cursor.fetchall()}
        if "asset" not in follower_columns:
            cursor.execute("ALTER TABLE followers ADD COLUMN asset TEXT")
        if "user_wallet_address" not in follower_columns:
            cursor.execute("ALTER TABLE followers ADD COLUMN user_wallet_address TEXT")
        if "stop_loss_pct" not in follower_columns:
            cursor.execute("ALTER TABLE followers ADD COLUMN stop_loss_pct REAL DEFAULT 10.0")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                wallet_id TEXT NOT NULL,
                wallet_address TEXT NOT NULL,
                referral_code TEXT NOT NULL UNIQUE,
                referred_by TEXT,
                created_at TEXT NOT NULL,
                email TEXT,
                telegram_chat_id TEXT,
                display_name TEXT,
                avatar_url TEXT,
                trade_alerts INTEGER DEFAULT 1,
                daily_summary INTEGER DEFAULT 1
            )
        ''')
        cursor.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in cursor.fetchall()}
        if "email" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "telegram_chat_id" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN telegram_chat_id TEXT")
        if "display_name" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        if "avatar_url" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
        if "trade_alerts" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN trade_alerts INTEGER DEFAULT 1")
        if "daily_summary" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN daily_summary INTEGER DEFAULT 1")

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
            CREATE TABLE IF NOT EXISTS social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                post_text TEXT NOT NULL,
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
        f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users WHERE user_id = {_PH}",
        (user_id,),
    )
    row = _row(cursor)
    conn.close()
    return row


def get_user_by_referral_code(referral_code):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users WHERE referral_code = {_PH}",
        (referral_code,),
    )
    row = _row(cursor)
    conn.close()
    return row


def get_user_by_wallet_id(wallet_id):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users WHERE wallet_id = {_PH}",
        (wallet_id,),
    )
    row = _row(cursor)
    conn.close()
    return row


def get_all_users():
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users ORDER BY created_at DESC"
    )
    rows = _rows(cursor)
    conn.close()
    return rows


def update_user_profile(user_id, **fields):
    allowed = {
        "wallet_id",
        "wallet_address",
        "referral_code",
        "referred_by",
        "email",
        "telegram_chat_id",
        "display_name",
        "avatar_url",
        "trade_alerts",
        "daily_summary",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_user(user_id)

    conn = _connect()
    cursor = _cursor(conn)
    assignments = ", ".join(f"{key} = {_PH}" for key in updates)
    cursor.execute(
        f"UPDATE users SET {assignments} WHERE user_id = {_PH}",
        (*updates.values(), user_id),
    )
    conn.commit()
    conn.close()
    return get_user(user_id)


def upsert_user_wallet(
    user_id,
    wallet_id,
    wallet_address,
    referral_code,
    referred_by=None,
    email=None,
    display_name=None,
    avatar_url=None,
    telegram_chat_id=None,
):
    existing = get_user(user_id)
    if existing:
        return update_user_profile(
            user_id,
            wallet_id=wallet_id or existing["wallet_id"],
            wallet_address=wallet_address or existing["wallet_address"],
            referral_code=referral_code or existing["referral_code"],
            referred_by=referred_by if referred_by is not None else existing.get("referred_by"),
            email=email if email is not None else existing.get("email"),
            display_name=display_name if display_name is not None else existing.get("display_name"),
            avatar_url=avatar_url if avatar_url is not None else existing.get("avatar_url"),
            telegram_chat_id=telegram_chat_id if telegram_chat_id is not None else existing.get("telegram_chat_id"),
        )

    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO users (user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (
            user_id,
            wallet_id,
            wallet_address,
            referral_code,
            referred_by,
            datetime.now(timezone.utc).isoformat(),
            email,
            telegram_chat_id,
            display_name,
            avatar_url,
            1,
            1,
        ),
    )
    conn.commit()
    conn.close()
    return get_user(user_id)


def set_user_preferences(user_id, trade_alerts=None, daily_summary=None):
    updates = {}
    if trade_alerts is not None:
        updates["trade_alerts"] = 1 if str(trade_alerts).lower() in {"1", "true", "yes", "on"} else 0
    if daily_summary is not None:
        updates["daily_summary"] = 1 if str(daily_summary).lower() in {"1", "true", "yes", "on"} else 0
    if not updates:
        return get_user(user_id)
    return update_user_profile(user_id, **updates)


def get_user_preferences(user_id):
    user = get_user(user_id) or {}
    return {
        "trade_alerts": int(user.get("trade_alerts") or 0),
        "daily_summary": int(user.get("daily_summary") or 0),
    }


def get_user_allocations(user_id):
    conn = _connect()
    cursor = _cursor(conn)
    rows = _select_follower_rows(
        cursor,
        f"SELECT target_agent, allocation_amount, asset, stop_loss_pct, user_wallet_id, user_wallet_address FROM followers WHERE user_id = {_PH} AND is_active = 1 ORDER BY id DESC",
        (user_id,),
    )
    conn.close()
    return rows


def deactivate_follower(user_id, target_agent):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"UPDATE followers SET is_active = 0 WHERE user_id = {_PH} AND target_agent = {_PH} AND is_active = 1",
        (user_id, target_agent),
    )
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    return updated > 0


# ── Referrals ─────────────────────────────────────────────────────────────────

def add_referral_reward(referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id=None):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO referral_rewards (referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id, timestamp) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id, datetime.now(timezone.utc).isoformat()),
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

def add_follower(
    user_id,
    user_wallet_id,
    target_agent,
    allocation_amount,
    asset=None,
    user_wallet_address=None,
    stop_loss_pct: float | None = 10.0,
):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO followers (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct if stop_loss_pct is not None else 10.0),
    )
    conn.commit()
    conn.close()
    log.info("User %s is now following %s.", user_id, target_agent)


def get_active_followers(agent_name):
    conn = _connect()
    cursor = _cursor(conn)
    rows = _select_follower_rows(
        cursor,
        f"SELECT user_wallet_id, user_wallet_address, allocation_amount, stop_loss_pct FROM followers WHERE target_agent = {_PH} AND is_active = 1",
        (agent_name,),
    )
    conn.close()
    return [(r["user_wallet_id"], r["user_wallet_address"], r["allocation_amount"], r.get("stop_loss_pct")) for r in rows]


def get_active_follower_rows(agent_name):
    conn = _connect()
    cursor = _cursor(conn)
    rows = _select_follower_rows(
        cursor,
        f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, is_active FROM followers WHERE target_agent = {_PH} AND is_active = 1 ORDER BY id DESC",
        (agent_name,),
    )
    conn.close()
    return rows


def get_follower_wallet(agent_name, user_id):
    conn = _connect()
    cursor = _cursor(conn)
    rows = _select_follower_rows(
        cursor,
        f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, is_active FROM followers WHERE target_agent = {_PH} AND user_id = {_PH} AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (agent_name, user_id),
    )
    row = rows[0] if rows else None
    conn.close()
    return row


def get_follower_by_wallet_id(agent_name, wallet_id):
    conn = _connect()
    cursor = _cursor(conn)
    rows = _select_follower_rows(
        cursor,
        f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, is_active FROM followers WHERE target_agent = {_PH} AND user_wallet_id = {_PH} AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (agent_name, wallet_id),
    )
    row = rows[0] if rows else None
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
        (agent, action, asset, tx_id, reason, datetime.now(timezone.utc).isoformat()),
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
        sorted_actions = sorted(actions)
        placeholders = ",".join(_PH for _ in sorted_actions)
        where.append(f"action IN ({placeholders})")
        params.extend(sorted_actions)

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    cursor.execute(
        f"SELECT id, agent, action, asset, tx_id, reason, timestamp FROM trade_history {where_clause} ORDER BY id DESC LIMIT {_PH}",
        params,
    )
    rows = _rows(cursor)
    conn.close()
    return rows


def get_latest_trade(agent: str) -> dict | None:
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT id, agent, action, asset, tx_id, reason, timestamp FROM trade_history WHERE agent = {_PH} ORDER BY id DESC LIMIT 1",
        (agent,),
    )
    row = _row(cursor)
    conn.close()
    return row


def follower_trade_agent_key(wallet_id: str, target_agent: str | None = None) -> str:
    base = f"Follower:{wallet_id}"
    return f"{base}:{target_agent}" if target_agent else base


def get_follower_trade_history(
    wallet_id: str,
    limit: int = 50,
    actions: set[str] | None = None,
    target_agent: str | None = None,
) -> list[dict]:
    conn = _connect()
    cursor = _cursor(conn)
    params: list = []
    where: list[str] = []
    agent_key = follower_trade_agent_key(wallet_id, target_agent)
    if target_agent:
        where.append(f"agent = {_PH}")
        params.append(agent_key)
    else:
        where.append(f"(agent = {_PH} OR agent LIKE {_PH})")
        params.extend([agent_key, f"{agent_key}:%"])
    if actions:
        sorted_actions = sorted(actions)
        placeholders = ",".join(_PH for _ in sorted_actions)
        where.append(f"action IN ({placeholders})")
        params.extend(sorted_actions)

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    cursor.execute(
        f"SELECT id, agent, action, asset, tx_id, reason, timestamp FROM trade_history {where_clause} ORDER BY id DESC LIMIT {_PH}",
        params,
    )
    rows = _rows(cursor)
    conn.close()
    return rows


def get_latest_follower_trade(wallet_id: str, target_agent: str | None = None) -> dict | None:
    conn = _connect()
    cursor = _cursor(conn)
    agent_key = follower_trade_agent_key(wallet_id, target_agent)
    if target_agent:
        cursor.execute(
            f"SELECT id, agent, action, asset, tx_id, reason, timestamp FROM trade_history WHERE agent = {_PH} ORDER BY id DESC LIMIT 1",
            (agent_key,),
        )
    else:
        cursor.execute(
            f"SELECT id, agent, action, asset, tx_id, reason, timestamp FROM trade_history WHERE (agent = {_PH} OR agent LIKE {_PH}) ORDER BY id DESC LIMIT 1",
            (agent_key, f"{agent_key}:%"),
        )
    row = _row(cursor)
    conn.close()
    return row


def log_social_post(agent: str, action: str, post_text: str, tx_id: str | None = None, reason: str = ""):
    conn = _connect()
    cursor = _cursor(conn)
    cursor.execute(
        f"INSERT INTO social_posts (agent, action, post_text, tx_id, reason, timestamp) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
        (agent, action, post_text, tx_id, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_social_posts(limit: int = 20, agent: str | None = None) -> list[dict]:
    conn = _connect()
    cursor = _cursor(conn)
    params: list = []
    where: list[str] = []
    if agent:
        where.append(f"agent = {_PH}")
        params.append(agent)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    cursor.execute(
        f"SELECT id, agent, action, post_text, tx_id, reason, timestamp FROM social_posts {where_clause} ORDER BY id DESC LIMIT {_PH}",
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
