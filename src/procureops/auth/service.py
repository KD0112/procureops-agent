from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from procureops.storage import SQLiteDatabase

PASSWORD_ITERATIONS = 310_000


class AuthIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    email: str
    display_name: str
    roles: frozenset[str]


class AuthSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: str
    expires_at: datetime
    identity: AuthIdentity


class _PasswordDigest(NamedTuple):
    salt: str
    digest: str


class AuthService:
    """Small local identity provider; API roles always come from server-side membership."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def bootstrap_demo_users(self, *, tenant_id: str, password: str) -> None:
        demo_users = (
            (
                "local-buyer",
                "buyer@procureops.local",
                "采购申请人",
                frozenset({"procurement_operator"}),
            ),
            (
                "local-approver",
                "approver@procureops.local",
                "部门审批人",
                frozenset({"procurement_operator", "department_approver"}),
            ),
            (
                "local-compliance",
                "compliance@procureops.local",
                "合规审批人",
                frozenset(
                    {
                        "procurement_operator",
                        "department_approver",
                        "compliance_approver",
                    }
                ),
            ),
        )
        for user_id, email, display_name, roles in demo_users:
            try:
                self.identity(user_id=user_id, tenant_id=tenant_id)
            except KeyError:
                pass
            else:
                continue
            self.create_user(
                user_id=user_id,
                email=email,
                display_name=display_name,
                password=password,
                tenant_id=tenant_id,
                roles=roles,
                if_missing=True,
            )

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str,
        password: str,
        tenant_id: str,
        roles: frozenset[str],
        if_missing: bool = False,
    ) -> AuthIdentity:
        if len(password) < 12:
            raise ValueError("local password must contain at least 12 characters")
        if not roles:
            raise ValueError("at least one tenant role is required")
        digest = self._password_digest(password)
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            if if_missing:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO local_users(
                        user_id, email, display_name, password_salt,
                        password_hash, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (user_id, email.casefold(), display_name, digest.salt, digest.digest, now),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO local_users(
                        user_id, email, display_name, password_salt,
                        password_hash, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (user_id, email.casefold(), display_name, digest.salt, digest.digest, now),
                )
            connection.execute(
                """
                INSERT INTO tenant_memberships(
                    tenant_id, user_id, roles_json, active, created_at
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(tenant_id, user_id) DO UPDATE SET
                    roles_json=excluded.roles_json, active=1
                """,
                (tenant_id, user_id, json.dumps(sorted(roles)), now),
            )
        return self.identity(user_id=user_id, tenant_id=tenant_id)

    def login(
        self,
        *,
        email: str,
        password: str,
        tenant_id: str,
        ttl: timedelta = timedelta(hours=8),
    ) -> AuthSession:
        now = datetime.now(UTC)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT u.user_id, u.password_salt, u.password_hash
                FROM local_users AS u
                JOIN tenant_memberships AS m ON m.user_id=u.user_id
                WHERE u.email=? AND u.active=1 AND m.tenant_id=? AND m.active=1
                """,
                (email.casefold().strip(), tenant_id),
            ).fetchone()
        if row is None or not self._verify_password(
            password,
            salt=row["password_salt"] if row else "0" * 32,
            expected=row["password_hash"] if row else "0" * 64,
        ):
            raise PermissionError("invalid local credentials")
        token = secrets.token_urlsafe(32)
        expires_at = now + ttl
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions(
                    session_id, user_id, tenant_id, token_hash,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    row["user_id"],
                    tenant_id,
                    self._token_hash(token),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return AuthSession(
            token=token,
            expires_at=expires_at,
            identity=self.identity(user_id=row["user_id"], tenant_id=tenant_id),
        )

    def resolve(self, *, token: str) -> AuthIdentity:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT s.user_id, s.tenant_id
                FROM auth_sessions AS s
                JOIN local_users AS u ON u.user_id=s.user_id AND u.active=1
                JOIN tenant_memberships AS m
                  ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id AND m.active=1
                WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?
                """,
                (self._token_hash(token), now),
            ).fetchone()
        if row is None:
            raise PermissionError("invalid or expired local session")
        return self.identity(user_id=row["user_id"], tenant_id=row["tenant_id"])

    def logout(self, *, token: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (datetime.now(UTC).isoformat(), self._token_hash(token)),
            )

    def identity(self, *, user_id: str, tenant_id: str) -> AuthIdentity:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT u.user_id, u.email, u.display_name, m.tenant_id, m.roles_json
                FROM local_users AS u
                JOIN tenant_memberships AS m ON m.user_id=u.user_id
                WHERE u.user_id=? AND m.tenant_id=? AND u.active=1 AND m.active=1
                """,
                (user_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError("active local identity not found")
        return AuthIdentity(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            email=row["email"],
            display_name=row["display_name"],
            roles=frozenset(json.loads(row["roles_json"])),
        )

    @staticmethod
    def _password_digest(password: str, *, salt: str | None = None) -> _PasswordDigest:
        normalized_salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(normalized_salt),
            PASSWORD_ITERATIONS,
        ).hex()
        return _PasswordDigest(normalized_salt, digest)

    @classmethod
    def _verify_password(cls, password: str, *, salt: str, expected: str) -> bool:
        actual = cls._password_digest(password, salt=salt).digest
        return hmac.compare_digest(actual, expected)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
