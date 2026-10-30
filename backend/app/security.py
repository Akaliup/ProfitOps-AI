from dataclasses import dataclass

from fastapi import Request

from .errors import DomainError


@dataclass(frozen=True)
class Actor:
    id: str
    role: str


def require_role(actor: Actor, *allowed: str):
    if actor.role not in allowed:
        raise DomainError("forbidden", "当前身份无权执行此操作", 403, {"required_roles": list(allowed)})


def get_actor(request: Request) -> Actor:
    settings = request.app.state.settings
    if not settings.demo_mode:
        raise DomainError("authentication_unavailable", "演示认证已关闭；需接入生产身份提供方", 503)
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise DomainError("unauthorized", "请使用本地演示身份登录", 401)
    token = authorization[7:]
    identities = {
        settings.analyst_token: Actor("demo-analyst", "analyst"),
        settings.approver_token: Actor("demo-approver", "approver"),
    }
    actor = identities.get(token)
    if actor is None:
        raise DomainError("unauthorized", "演示凭据无效", 401)
    return actor
