from .models import AuditLog
from .security import Actor


def record(session, actor: Actor | str, action: str, object_type: str, object_id: str, request_id: str, outcome="success", details=None):
    session.add(AuditLog(
        actor=actor.id if isinstance(actor, Actor) else actor,
        action=action, object_type=object_type, object_id=object_id,
        request_id=request_id, outcome=outcome, details=details or {},
    ))


def record_separately(factory, *args, **kwargs):
    with factory.begin() as session:
        record(session, *args, **kwargs)
