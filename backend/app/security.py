import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select

from .errors import DomainError
from .models import Tenant, User

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
DEFAULT_AUTH_SECRET = "dev-only-change-me"


@dataclass(frozen=True)
class Actor:
    id: str
    role: str
    tenant_id: str = "demo"


def require_role(actor: Actor, *allowed: str):
    if actor.role not in allowed:
        raise DomainError("forbidden", "当前身份无权执行此操作", 403, {"required_roles": list(allowed)})


def scoped_idempotency_key(tenant_id: str, key: str) -> str:
    """Namespace idempotency keys so one tenant cannot occupy another's key.

    The database unique constraint also includes ``tenant_id``; the prefix is
    kept so rows created before multi-tenancy still replay with their original
    client key. The demo tenant keeps the raw key for the same reason.
    """
    return key if tenant_id == "demo" else f"{tenant_id}:{key}"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a per-password random salt."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=_b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(_b64decode(expected)),
        )
        return hmac.compare_digest(digest, _b64decode(expected))
    except (ValueError, TypeError):
        return False


_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def create_access_token(actor: Actor, settings, now: datetime | None = None) -> tuple[str, int]:
    now = now or datetime.now(timezone.utc)
    expires_in = settings.auth_token_ttl_seconds
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": actor.id, "role": actor.role, "tenant_id": actor.tenant_id,
        "iss": settings.auth_issuer, "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    signing_input = ".".join([
        _b64encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
        _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
    ])
    signature = hmac.new(
        settings.auth_secret.get_secret_value().encode("utf-8"),
        signing_input.encode("ascii"), hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64encode(signature)}", expires_in


def decode_access_token(token: str, settings) -> dict:
    try:
        header_part, payload_part, signature_part = token.split(".")
        signing_input = f"{header_part}.{payload_part}"
        expected = hmac.new(
            settings.auth_secret.get_secret_value().encode("utf-8"),
            signing_input.encode("ascii"), hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(signature_part)):
            raise ValueError("signature")
        header = json.loads(_b64decode(header_part))
        payload = json.loads(_b64decode(payload_part))
        if header.get("alg") != "HS256":
            raise ValueError("algorithm")
        if payload.get("iss") != settings.auth_issuer:
            raise ValueError("issuer")
        if int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("expired")
        if not payload.get("sub") or not payload.get("tenant_id"):
            raise ValueError("subject")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, KeyError):
        raise DomainError("unauthorized", "登录凭据无效或已过期", 401) from None


def authenticate(factory, email: str, password: str, tenant_id: str | None = None) -> Actor:
    normalized = email.strip().lower()
    with factory() as session:
        query = select(User).join(Tenant, User.tenant_id == Tenant.id).where(
            User.email == normalized, User.active.is_(True), Tenant.active.is_(True),
        )
        if tenant_id:
            query = query.where(User.tenant_id == tenant_id)
        users = session.scalars(query).all()
        for user in users:
            if verify_password(password, user.password_hash):
                return Actor(user.id, user.role, user.tenant_id)
    # Keep the failure path roughly constant-time whether or not the email exists.
    verify_password(password, _DUMMY_PASSWORD_HASH)
    raise DomainError("invalid_credentials", "邮箱或密码错误", 401)


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    return authorization[7:] if authorization.startswith("Bearer ") else None


def rate_limit_identity(token: str | None, settings) -> str | None:
    """Return a stable identity for rate limiting, or ``None`` if anonymous.

    Only credentials this service would actually accept may mint a separate
    bucket. Arbitrary bearer values therefore cannot rotate around the base
    limit; they fall back to the shared anonymous bucket. The returned value
    is hashed by the limiter and is never logged or persisted.
    """
    if token is None:
        return None
    if settings.demo_mode and token in (settings.analyst_token, settings.approver_token):
        return token
    try:
        payload = decode_access_token(token, settings)
    except DomainError:
        return None
    return f"{payload['tenant_id']}:{payload['sub']}"


def get_actor(request: Request) -> Actor:
    settings = request.app.state.settings
    token = _bearer_token(request)
    if token is None:
        raise DomainError("unauthorized", "请先登录", 401)
    if settings.demo_mode:
        identities = {
            settings.analyst_token: Actor("demo-analyst", "analyst", "demo"),
            settings.approver_token: Actor("demo-approver", "approver", "demo"),
        }
        if token in identities:
            return identities[token]
    payload = decode_access_token(token, settings)
    with request.app.state.factory() as session:
        user = session.scalar(
            select(User).join(Tenant, User.tenant_id == Tenant.id).where(
                User.id == payload["sub"], User.tenant_id == payload["tenant_id"],
                User.active.is_(True), Tenant.active.is_(True),
            )
        )
        if user is None:
            raise DomainError("unauthorized", "登录用户不存在或已停用", 401)
        return Actor(user.id, user.role, user.tenant_id)


def get_optional_actor(request: Request) -> Actor:
    """Anonymous bootstrap requests see the demo tenant only."""
    if _bearer_token(request) is None:
        return Actor("anonymous", "viewer", "demo")
    return get_actor(request)
