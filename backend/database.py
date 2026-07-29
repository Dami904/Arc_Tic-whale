import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
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
    import psycopg2.pool

DB_NAME = os.path.join(os.path.dirname(__file__), "agora_marketplace.db")

_pg_pool = None


def _get_pg_pool():
    # Lazy singleton: a single request can open a dozen+ short DB calls
    # (dashboard stats, trade history, performance, etc). Without pooling
    # each one paid a fresh TCP+TLS handshake to Neon, which is what made
    # the app feel slow to load right after login. Reusing connections
    # from a small pool removes that overhead from the hot path.
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)
    return _pg_pool


def _connect():
    if _USE_PG:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _connection():
    """Borrow a DB connection and guarantee it is returned - even on exception.

    Postgres connections come from a pool (see _get_pg_pool); SQLite files
    are cheap to open locally so those still get a fresh connection each time.
    """
    if _USE_PG:
        pool = _get_pg_pool()
        conn = pool.getconn()
        try:
            yield conn
        finally:
            # rollback() is a no-op if the caller already committed; it's
            # what clears out read-only queries' implicit transaction so
            # the connection goes back to the pool clean.
            try:
                conn.rollback()
            except Exception:
                pass
            pool.putconn(conn)
        return
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


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
    with _connection() as conn:
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
            cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS followed_at TEXT")
            cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS remaining_capital DOUBLE PRECISION")
            cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS deactivated_at TEXT")

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
            cursor.execute("ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS price DOUBLE PRECISION")
            cursor.execute("ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS amount_usdc DOUBLE PRECISION")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_nav_snapshots (
                    id SERIAL PRIMARY KEY,
                    agent TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    nav_multiplier DOUBLE PRECISION NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (agent, snapshot_date)
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
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS auth_nonces (
                    nonce TEXT PRIMARY KEY,
                    expires_at DOUBLE PRECISION NOT NULL
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
            if "followed_at" not in follower_columns:
                cursor.execute("ALTER TABLE followers ADD COLUMN followed_at TEXT")
            if "remaining_capital" not in follower_columns:
                cursor.execute("ALTER TABLE followers ADD COLUMN remaining_capital REAL")
            if "deactivated_at" not in follower_columns:
                cursor.execute("ALTER TABLE followers ADD COLUMN deactivated_at TEXT")

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
            cursor.execute("PRAGMA table_info(trade_history)")
            trade_history_columns = {row[1] for row in cursor.fetchall()}
            if "price" not in trade_history_columns:
                cursor.execute("ALTER TABLE trade_history ADD COLUMN price REAL")
            if "amount_usdc" not in trade_history_columns:
                cursor.execute("ALTER TABLE trade_history ADD COLUMN amount_usdc REAL")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_nav_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    nav_multiplier REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (agent, snapshot_date)
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
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS auth_nonces (
                    nonce TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL
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
    log.info("Database initialised (%s).", "postgresql" if _USE_PG else "sqlite")


# ── Users ─────────────────────────────────────────────────────────────────────

def get_user(user_id):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users WHERE user_id = {_PH}",
            (user_id,),
        )
        return _row(cursor)


def get_user_by_referral_code(referral_code):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users WHERE referral_code = {_PH}",
            (referral_code,),
        )
        return _row(cursor)


def get_user_by_wallet_id(wallet_id):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users WHERE wallet_id = {_PH}",
            (wallet_id,),
        )
        return _row(cursor)


def get_all_users():
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            "SELECT user_id, wallet_id, wallet_address, referral_code, referred_by, created_at, email, telegram_chat_id, display_name, avatar_url, trade_alerts, daily_summary FROM users ORDER BY created_at DESC"
        )
        return _rows(cursor)


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

    with _connection() as conn:
        cursor = _cursor(conn)
        assignments = ", ".join(f"{key} = {_PH}" for key in updates)
        cursor.execute(
            f"UPDATE users SET {assignments} WHERE user_id = {_PH}",
            (*updates.values(), user_id),
        )
        conn.commit()
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

    with _connection() as conn:
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
    with _connection() as conn:
        cursor = _cursor(conn)
        rows = _select_follower_rows(
            cursor,
            f"SELECT target_agent, allocation_amount, asset, stop_loss_pct, user_wallet_id, user_wallet_address, "
            f"COALESCE(remaining_capital, allocation_amount) AS remaining_capital "
            f"FROM followers WHERE user_id = {_PH} AND is_active = 1 ORDER BY id DESC",
            (user_id,),
        )
    return rows


def deactivate_follower(user_id, target_agent):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"UPDATE followers SET is_active = 0, deactivated_at = {_PH} "
            f"WHERE user_id = {_PH} AND target_agent = {_PH} AND is_active = 1",
            (datetime.now(timezone.utc).isoformat(), user_id, target_agent),
        )
        conn.commit()
        updated = cursor.rowcount
    return updated > 0


def update_follower_stop_loss(user_id: str, target_agent: str, stop_loss_pct: float) -> bool:
    """Update the stop-loss threshold for an active follower allocation."""
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"UPDATE followers SET stop_loss_pct = {_PH} WHERE user_id = {_PH} AND target_agent = {_PH} AND is_active = 1",
            (stop_loss_pct, user_id, target_agent),
        )
        conn.commit()
        updated = cursor.rowcount
    return updated > 0


# ── Referrals ─────────────────────────────────────────────────────────────────

def add_referral_reward(referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id=None):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"INSERT INTO referral_rewards (referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id, timestamp) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
            (referrer_user_id, referred_user_id, profit_amount, reward_amount, tx_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_referral_summary(user_id):
    with _connection() as conn:
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
    with _connection() as conn:
        cursor = _cursor(conn)
        # Re-following replaces the previous follow instead of stacking a
        # duplicate active row (each active row would otherwise get its own
        # mirrored trade per signal). The trade stream is keyed by
        # wallet+agent, so an open position carries over to the new follow.
        cursor.execute(
            f"UPDATE followers SET is_active = 0, deactivated_at = {_PH} "
            f"WHERE user_id = {_PH} AND target_agent = {_PH} AND is_active = 1",
            (datetime.now(timezone.utc).isoformat(), user_id, target_agent),
        )
        if cursor.rowcount:
            log.info("Replacing %d existing active follow(s) of %s for user %s.",
                     cursor.rowcount, target_agent, user_id)
        cursor.execute(
            f"INSERT INTO followers (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, followed_at, remaining_capital) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
            (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset,
             stop_loss_pct if stop_loss_pct is not None else 10.0, datetime.now(timezone.utc).isoformat(),
             allocation_amount),
        )
        conn.commit()
    log.info("User %s is now following %s.", user_id, target_agent)


def adjust_follower_remaining_capital(user_wallet_id: str, target_agent: str, delta: float) -> None:
    """
    Move a follower's deployable USDC pool: negative delta on a BUY (capital
    deployed into the position), positive on a SELL (proceeds returned).
    Floored at 0. Legacy rows with NULL remaining_capital are initialised
    from allocation_amount before applying the delta.
    """
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT COALESCE(remaining_capital, allocation_amount) AS remaining FROM followers "
            f"WHERE user_wallet_id = {_PH} AND target_agent = {_PH} AND is_active = 1",
            (user_wallet_id, target_agent),
        )
        row = _row(cursor)
        if not row:
            return
        new_remaining = round(max(0.0, float(row["remaining"] or 0.0) + delta), 2)
        cursor.execute(
            f"UPDATE followers SET remaining_capital = {_PH} "
            f"WHERE user_wallet_id = {_PH} AND target_agent = {_PH} AND is_active = 1",
            (new_remaining, user_wallet_id, target_agent),
        )
        conn.commit()


def get_active_followers(agent_name):
    with _connection() as conn:
        cursor = _cursor(conn)
        rows = _select_follower_rows(
            cursor,
            f"SELECT user_wallet_id, user_wallet_address, allocation_amount, stop_loss_pct, "
            f"COALESCE(remaining_capital, allocation_amount) AS remaining_capital "
            f"FROM followers WHERE target_agent = {_PH} AND is_active = 1",
            (agent_name,),
        )
    return [(r["user_wallet_id"], r["user_wallet_address"], r["allocation_amount"], r.get("stop_loss_pct"),
             r.get("remaining_capital", r["allocation_amount"])) for r in rows]


def get_active_follower_rows(agent_name):
    with _connection() as conn:
        cursor = _cursor(conn)
        rows = _select_follower_rows(
            cursor,
            f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, is_active, "
            f"COALESCE(remaining_capital, allocation_amount) AS remaining_capital "
            f"FROM followers WHERE target_agent = {_PH} AND is_active = 1 ORDER BY id DESC",
            (agent_name,),
        )
    return rows


def get_follower_wallet(agent_name, user_id):
    with _connection() as conn:
        cursor = _cursor(conn)
        rows = _select_follower_rows(
            cursor,
            f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, is_active FROM followers WHERE target_agent = {_PH} AND user_id = {_PH} AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (agent_name, user_id),
        )
    return rows[0] if rows else None


def get_follower_by_wallet_id(agent_name, wallet_id):
    with _connection() as conn:
        cursor = _cursor(conn)
        rows = _select_follower_rows(
            cursor,
            f"SELECT user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, is_active FROM followers WHERE target_agent = {_PH} AND user_wallet_id = {_PH} AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (agent_name, wallet_id),
        )
    return rows[0] if rows else None


def get_follower_summary(agent_name):
    with _connection() as conn:
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
    return summary


def get_agent_retention_stats(agent_name: str) -> dict:
    """
    total_follows / active_follows count every follow ever created for this
    agent (not just currently active ones), so retention_rate_pct reflects
    real churn. avg_tenure_days is computed only from follows that have both
    followed_at and deactivated_at recorded - legacy rows predating those
    columns are excluded rather than guessed at.
    """
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT is_active, followed_at, deactivated_at FROM followers WHERE target_agent = {_PH}",
            (agent_name,),
        )
        rows = _rows(cursor)

    total_follows = len(rows)
    if total_follows == 0:
        return {"total_follows": 0, "active_follows": 0, "retention_rate_pct": None, "avg_tenure_days": None}

    active_follows = sum(1 for r in rows if int(r.get("is_active") or 0) == 1)
    retention_rate_pct = round((active_follows / total_follows) * 100, 1)

    tenures = []
    for r in rows:
        followed_at = r.get("followed_at")
        deactivated_at = r.get("deactivated_at")
        if not followed_at or not deactivated_at:
            continue
        try:
            start = datetime.fromisoformat(followed_at)
            end = datetime.fromisoformat(deactivated_at)
        except ValueError:
            continue
        tenures.append((end - start).total_seconds() / 86400)

    avg_tenure_days = round(sum(tenures) / len(tenures), 1) if tenures else None

    return {
        "total_follows": total_follows,
        "active_follows": active_follows,
        "retention_rate_pct": retention_rate_pct,
        "avg_tenure_days": avg_tenure_days,
    }


# ── Trade history ─────────────────────────────────────────────────────────────

def log_trade(agent: str, action: str, asset: str | None, tx_id: str | None, reason: str = "", price: float | None = None, amount_usdc: float | None = None):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"INSERT INTO trade_history (agent, action, asset, tx_id, reason, timestamp, price, amount_usdc) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
            (agent, action, asset, tx_id, reason, datetime.now(timezone.utc).isoformat(), price, amount_usdc),
        )
        conn.commit()


def get_trade_history(limit: int = 50, agent: str | None = None, actions: set[str] | None = None) -> list[dict]:
    with _connection() as conn:
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
        return _rows(cursor)


def get_latest_trade(agent: str) -> dict | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT id, agent, action, asset, tx_id, reason, timestamp FROM trade_history WHERE agent = {_PH} ORDER BY id DESC LIMIT 1",
            (agent,),
        )
        return _row(cursor)


def follower_trade_agent_key(wallet_id: str, target_agent: str | None = None) -> str:
    base = f"Follower:{wallet_id}"
    return f"{base}:{target_agent}" if target_agent else base


def get_follower_trade_history(
    wallet_id: str,
    limit: int = 50,
    actions: set[str] | None = None,
    target_agent: str | None = None,
) -> list[dict]:
    with _connection() as conn:
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
        return _rows(cursor)


def get_latest_follower_trade(wallet_id: str, target_agent: str | None = None) -> dict | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        agent_key = follower_trade_agent_key(wallet_id, target_agent)
        if target_agent:
            cursor.execute(
                f"SELECT id, agent, action, asset, tx_id, reason, timestamp, amount_usdc, price FROM trade_history WHERE agent = {_PH} ORDER BY id DESC LIMIT 1",
                (agent_key,),
            )
        else:
            cursor.execute(
                f"SELECT id, agent, action, asset, tx_id, reason, timestamp, amount_usdc, price FROM trade_history WHERE (agent = {_PH} OR agent LIKE {_PH}) ORDER BY id DESC LIMIT 1",
                (agent_key, f"{agent_key}:%"),
            )
        return _row(cursor)


def log_social_post(agent: str, action: str, post_text: str, tx_id: str | None = None, reason: str = ""):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"INSERT INTO social_posts (agent, action, post_text, tx_id, reason, timestamp) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
            (agent, action, post_text, tx_id, reason, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_social_posts(limit: int = 20, agent: str | None = None) -> list[dict]:
    with _connection() as conn:
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
        return _rows(cursor)


def get_trade_metrics(agent: str) -> dict:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"SELECT action, tx_id FROM trade_history WHERE agent = {_PH}", (agent,))
        rows = _rows(cursor)

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
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"SELECT value FROM settings WHERE key = {_PH}", (key,))
        row = _row(cursor)
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with _connection() as conn:
        cursor = _cursor(conn)
        if _USE_PG:
            cursor.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )
        else:
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def is_kill_switch_active() -> bool:
    return get_setting("kill_switch", "0") == "1"


def seconds_since_last_cycle() -> float | None:
    """Seconds since the most recent trade_history row (any agent, any action).
    Returns None if no cycle has ever run. Used to self-throttle the GitHub
    Actions trade-cycle job when the schedule trigger fires more often than
    intended."""
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute("SELECT MAX(timestamp) AS ts FROM trade_history")
        row = _row(cursor)
        ts = row["ts"] if row else None
        if not ts:
            return None
        last = datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds()


# ── Sessions & auth nonces (DB-backed so they survive restarts/sleep) ─────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str, ttl_seconds: int = 86_400) -> str:
    """Issue a new opaque session token for user_id. Only the hash is stored.
    Expired rows are cleaned up here so the read path stays a pure SELECT."""
    token = f"sess_{secrets.token_urlsafe(32)}"
    now = time.time()
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"DELETE FROM sessions WHERE expires_at < {_PH}", (now,))
        cursor.execute(
            f"INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ({_PH}, {_PH}, {_PH})",
            (_hash_token(token), user_id, now + ttl_seconds),
        )
        conn.commit()
    return token


def get_session_user(token: str) -> str | None:
    """Return the user_id for a valid session token, or None."""
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT user_id FROM sessions WHERE token_hash = {_PH} AND expires_at >= {_PH}",
            (_hash_token(token), time.time()),
        )
        row = _row(cursor)
    return row["user_id"] if row else None


def delete_session(token: str) -> None:
    """Revoke a single session (logout)."""
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"DELETE FROM sessions WHERE token_hash = {_PH}", (_hash_token(token),))
        conn.commit()


def delete_user_sessions(user_id: str) -> None:
    """Revoke every session for a user (compromise response / credential change)."""
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"DELETE FROM sessions WHERE user_id = {_PH}", (user_id,))
        conn.commit()


def create_auth_nonce(ttl_seconds: int = 300) -> str:
    nonce = secrets.token_urlsafe(24)
    now = time.time()
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"DELETE FROM auth_nonces WHERE expires_at < {_PH}", (now,))
        cursor.execute(
            f"INSERT INTO auth_nonces (nonce, expires_at) VALUES ({_PH}, {_PH})",
            (nonce, now + ttl_seconds),
        )
        conn.commit()
    return nonce


def consume_auth_nonce(nonce: str) -> bool:
    """Atomically delete the nonce; True only if it existed and was unexpired (single-use)."""
    now = time.time()
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"DELETE FROM auth_nonces WHERE expires_at < {_PH}", (now,))
        cursor.execute(f"DELETE FROM auth_nonces WHERE nonce = {_PH} AND expires_at >= {_PH}", (nonce, now))
        consumed = cursor.rowcount == 1
        conn.commit()
    return consumed


# ── Performance stats support ───────────────────────────────────────────────

def get_trade_rows_for_performance(agent: str, since: str | None = None) -> list[dict]:
    with _connection() as conn:
        cursor = _cursor(conn)
        if since:
            cursor.execute(
                f"SELECT action, asset, price, timestamp FROM trade_history "
                f"WHERE agent = {_PH} AND price IS NOT NULL AND timestamp >= {_PH} ORDER BY timestamp ASC",
                (agent, since),
            )
        else:
            cursor.execute(
                f"SELECT action, asset, price, timestamp FROM trade_history "
                f"WHERE agent = {_PH} AND price IS NOT NULL ORDER BY timestamp ASC",
                (agent,),
            )
        return _rows(cursor)


def get_price_at_or_before(agent: str, before_timestamp: str) -> float | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT price FROM trade_history WHERE agent = {_PH} AND price IS NOT NULL "
            f"AND timestamp <= {_PH} ORDER BY timestamp DESC LIMIT 1",
            (agent, before_timestamp),
        )
        row = _row(cursor)
    return row["price"] if row else None


def get_followed_at(user_id: str, target_agent: str) -> str | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT followed_at FROM followers WHERE user_id = {_PH} AND target_agent = {_PH} AND is_active = 1 "
            f"ORDER BY id DESC LIMIT 1",
            (user_id, target_agent),
        )
        row = _row(cursor)
    return row["followed_at"] if row else None


def upsert_nav_snapshot(agent: str, snapshot_date: str, nav_multiplier: float) -> None:
    with _connection() as conn:
        cursor = _cursor(conn)
        if _USE_PG:
            cursor.execute(
                "INSERT INTO agent_nav_snapshots (agent, snapshot_date, nav_multiplier, created_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (agent, snapshot_date) "
                "DO UPDATE SET nav_multiplier = EXCLUDED.nav_multiplier, created_at = EXCLUDED.created_at",
                (agent, snapshot_date, nav_multiplier, datetime.now(timezone.utc).isoformat()),
            )
        else:
            cursor.execute(
                "INSERT INTO agent_nav_snapshots (agent, snapshot_date, nav_multiplier, created_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (agent, snapshot_date) "
                "DO UPDATE SET nav_multiplier = excluded.nav_multiplier, created_at = excluded.created_at",
                (agent, snapshot_date, nav_multiplier, datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()


def get_nav_snapshot_on_or_before(agent: str, target_date: str) -> dict | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT nav_multiplier, snapshot_date FROM agent_nav_snapshots "
            f"WHERE agent = {_PH} AND snapshot_date <= {_PH} ORDER BY snapshot_date DESC LIMIT 1",
            (agent, target_date),
        )
        return _row(cursor)


if __name__ == "__main__":
    init_db()
