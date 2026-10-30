from datetime import datetime, timezone

from .models import Analysis, AuditLog, Execution, Recommendation


def iso_utc(value: datetime) -> str:
    """Serialize database timestamps as explicit UTC, independent of the database driver."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def analysis_view(row: Analysis, replayed=False):
    result = row.result or {}
    return {
        "id": row.id, "status": row.status, "request": row.request,
        "steps": row.steps, "result": row.result, "error": row.error,
        "recommendation_id": row.recommendation_id, "request_id": row.request_id,
        "created_at": iso_utc(row.created_at), "updated_at": iso_utc(row.updated_at),
        "replayed": replayed, "engine": result.get("engine", "deterministic-rules-v1"),
    }


def execution_view(row: Execution | None):
    if row is None:
        return None
    return {
        "execution_id": row.id, "recommendation_id": row.recommendation_id,
        "status": row.status, "simulation": row.simulation,
        "result": row.result, "error": row.error,
        "actor": row.actor, "request_id": row.request_id, "created_at": iso_utc(row.created_at),
    }


def recommendation_view(row: Recommendation, execution=None):
    return {
        "id": row.id, "analysis_id": row.analysis_id, "status": row.status,
        "title": row.title, "body": row.body, "action": row.action,
        "approved_by": row.approved_by, "approval_reason": row.approval_reason,
        "created_at": iso_utc(row.created_at), "updated_at": iso_utc(row.updated_at),
        "execution": execution_view(execution),
    }


def audit_view(row: AuditLog):
    return {
        "id": row.id, "actor": row.actor, "action": row.action,
        "object_type": row.object_type, "object_id": row.object_id,
        "request_id": row.request_id, "outcome": row.outcome,
        "details": row.details, "created_at": iso_utc(row.created_at),
    }
