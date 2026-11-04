"""Local accounts, opaque login sessions and explicit resource ownership.

Shares the RunStore SQLite file, not the ERP database. No pre-auth session is
adopted implicitly; ownership is issued with a fresh server-generated identifier.
"""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

COOKIE = "erpilot_login"
ROLES = {"operator", "approver"}
PBKDF2_ROUNDS = 600_000


class AuthError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


@dataclass(frozen=True)
class Identity:
    user_id: str
    username: str
    role: str

    def public(self):
        return asdict(self)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)


class AuthStore:
    def __init__(self, path, *, ttl=28800, clock=time.time):
        if not 60 <= ttl <= 604800:
            raise ValueError("login TTL must be between 60 seconds and 7 days")
        self.path, self.ttl, self.clock = Path(path), ttl, clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS auth_user (
                    user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, salt TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('operator','approver')),
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS auth_login (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_owned_session (
                    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_sessions_by_owner
                    ON auth_owned_session(user_id);
                CREATE TABLE IF NOT EXISTS auth_login_failure (
                    username TEXT NOT NULL, occurred REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_failures_by_user
                    ON auth_login_failure(username, occurred);
                CREATE TABLE IF NOT EXISTS auth_audit (
                    audit_id INTEGER PRIMARY KEY, user_id TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT NOT NULL,
                    detail TEXT NOT NULL, occurred REAL NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_user(self, username, password, role="operator"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{2,63}", username):
            raise ValueError("username must be 3–64 ASCII letters, numbers, _, . or -")
        if not 12 <= len(password) <= 256 or role not in ROLES:
            raise ValueError("password must be 12–256 characters; choose operator or approver")
        salt, user_id = secrets.token_hex(16), uuid4().hex
        encoded = password_hash(password, salt).hex()
        try:
            with self.connect() as db:
                db.execute(
                    "INSERT INTO auth_user VALUES (?,?,?,?,?,1)",
                    (user_id, username.lower(), encoded, salt, role),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("username already exists") from exc
        return Identity(user_id, username.lower(), role)

    def disable_user(self, username):
        with self.connect() as db:
            row = db.execute(
                "SELECT user_id FROM auth_user WHERE username=?", (username.lower(),)
            ).fetchone()
            if row is None:
                raise ValueError("unknown user")
            db.execute("UPDATE auth_user SET active=0 WHERE user_id=?", (row[0],))
            db.execute("DELETE FROM auth_login WHERE user_id=?", (row[0],))

    def login(self, username, password):
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or len(username) > 64
            or len(password) > 256
        ):
            raise AuthError(401, "invalid_credentials", "账号或密码不正确")
        username, now = username.lower(), self.clock()
        with self.connect() as db:
            # Serialize attempt reservation so parallel requests cannot bypass throttling.
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM auth_login_failure WHERE occurred<?", (now - 600,))
            attempts = db.execute(
                "SELECT COUNT(*) FROM auth_login_failure WHERE username=?", (username,)
            ).fetchone()[0]
            if attempts >= 5:
                raise AuthError(429, "login_throttled", "登录尝试过多，请十分钟后再试")
            db.execute("INSERT INTO auth_login_failure VALUES (?,?)", (username, now))
            row = db.execute("SELECT * FROM auth_user WHERE username=?", (username,)).fetchone()
        # Derive for unknown names too, without holding SQLite's write lock.
        value = password_hash(password, row["salt"] if row else "00" * 16)
        if (
            row is None
            or not row["active"]
            or not hmac.compare_digest(
                value.hex(),
                row["password_hash"],
            )
        ):
            raise AuthError(401, "invalid_credentials", "账号或密码不正确")
        identity = Identity(row["user_id"], row["username"], row["role"])
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            # A disable racing password derivation must not issue a usable session.
            active = db.execute(
                "SELECT active FROM auth_user WHERE user_id=?", (identity.user_id,)
            ).fetchone()
            if not active or not active[0]:
                raise AuthError(401, "invalid_credentials", "账号或密码不正确")
            db.execute("DELETE FROM auth_login WHERE expires<=?", (now,))
            db.execute(
                "INSERT INTO auth_login VALUES (?,?,?,?)",
                (token_hash(token), identity.user_id, csrf, now + self.ttl),
            )
            db.execute("DELETE FROM auth_login_failure WHERE username=?", (username,))
        self.audit(identity, "login", "account", {})
        return identity, token, csrf

    def authenticate(self, token):
        if not token or len(token) > 256:
            raise AuthError(401, "login_required", "请先登录")
        with self.connect() as db:
            row = db.execute(
                "SELECT u.user_id,u.username,u.role,l.csrf FROM auth_login l "
                "JOIN auth_user u ON u.user_id=l.user_id "
                "WHERE l.token_hash=? AND l.expires>? AND u.active=1",
                (token_hash(token), self.clock()),
            ).fetchone()
        if row is None:
            raise AuthError(401, "login_required", "登录已失效，请重新登录")
        return Identity(row["user_id"], row["username"], row["role"]), row["csrf"]

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM auth_login WHERE token_hash=?", (token_hash(token),))

    def new_session(self, identity):
        session_id = uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO auth_owned_session VALUES (?,?)", (session_id, identity.user_id)
            )
        return session_id

    def require_owner(self, identity, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise AuthError(404, "session_not_found", "会话不存在或无权访问")
        with self.connect() as db:
            row = db.execute(
                "SELECT user_id FROM auth_owned_session WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None or row[0] != identity.user_id:
            raise AuthError(404, "session_not_found", "会话不存在或无权访问")

    def session_tools(self, session_id, tools):
        with self.connect() as db:
            row = db.execute(
                "SELECT u.role,u.active FROM auth_user u JOIN auth_owned_session s "
                "ON s.user_id=u.user_id WHERE s.session_id=?",
                (session_id,),
            ).fetchone()
        if row is None or not row["active"]:
            return []
        return tools if row["role"] == "approver" else [t for t in tools if t.risk is None]

    def audit(self, identity, action, target, detail):
        with self.connect() as db:
            db.execute(
                "INSERT INTO auth_audit(user_id,action,target,detail,occurred) VALUES (?,?,?,?,?)",
                (
                    identity.user_id,
                    action,
                    target[:512],
                    json.dumps(detail, ensure_ascii=False),
                    self.clock(),
                ),
            )

    def audits(self, identity, limit=50):
        with self.connect() as db:
            rows = db.execute(
                "SELECT audit_id,action,target,detail,occurred FROM auth_audit "
                "WHERE user_id=? ORDER BY audit_id DESC LIMIT ?",
                (identity.user_id, limit),
            ).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]
