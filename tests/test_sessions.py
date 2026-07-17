import hashlib
import time

import pytest

import backend.database as db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    # Force the SQLite backend regardless of any DATABASE_URL in the
    # environment, so tests can never hit production Postgres.
    monkeypatch.setattr(db, "_USE_PG", False)
    monkeypatch.setattr(db, "_PH", "?")
    monkeypatch.setattr(db, "DB_NAME", str(tmp_path / "test.db"))
    db.init_db()
    yield db


class TestSessions:
    def test_create_and_lookup(self, tmp_db):
        token = tmp_db.create_session("user_abc", ttl_seconds=60)
        assert token.startswith("sess_")
        assert tmp_db.get_session_user(token) == "user_abc"

    def test_expired_session_returns_none(self, tmp_db):
        token = tmp_db.create_session("user_abc", ttl_seconds=-1)
        assert tmp_db.get_session_user(token) is None

    def test_unknown_token_returns_none(self, tmp_db):
        assert tmp_db.get_session_user("sess_doesnotexist") is None

    def test_token_stored_hashed(self, tmp_db):
        token = tmp_db.create_session("user_abc", ttl_seconds=60)
        with db._connection() as conn:
            cur = db._cursor(conn)
            cur.execute("SELECT token_hash FROM sessions")
            rows = db._rows(cur)
        assert len(rows) == 1
        assert rows[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()

    def test_delete_session_revokes_token(self, tmp_db):
        token = tmp_db.create_session("user_abc", ttl_seconds=60)
        assert tmp_db.get_session_user(token) == "user_abc"
        tmp_db.delete_session(token)
        assert tmp_db.get_session_user(token) is None

    def test_delete_user_sessions_revokes_all_for_user(self, tmp_db):
        token_a = tmp_db.create_session("user_abc", ttl_seconds=60)
        token_b = tmp_db.create_session("user_abc", ttl_seconds=60)
        token_other = tmp_db.create_session("user_xyz", ttl_seconds=60)
        tmp_db.delete_user_sessions("user_abc")
        assert tmp_db.get_session_user(token_a) is None
        assert tmp_db.get_session_user(token_b) is None
        assert tmp_db.get_session_user(token_other) == "user_xyz"


class TestAuthNonces:
    def test_consume_valid_nonce_once(self, tmp_db):
        nonce = tmp_db.create_auth_nonce(ttl_seconds=300)
        assert tmp_db.consume_auth_nonce(nonce) is True
        assert tmp_db.consume_auth_nonce(nonce) is False  # single-use

    def test_expired_nonce_rejected(self, tmp_db):
        nonce = tmp_db.create_auth_nonce(ttl_seconds=-1)
        assert tmp_db.consume_auth_nonce(nonce) is False

    def test_unknown_nonce_rejected(self, tmp_db):
        assert tmp_db.consume_auth_nonce("nope") is False
