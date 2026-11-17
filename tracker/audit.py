"""Запись аудита. В MVP actor может быть None — вход появится на этапе 2."""

from __future__ import annotations

from typing import Any

from .models import AuditLog


def log_audit(
    *,
    actor,
    action: str,
    entity_type: str,
    entity_id: Any = "",
    summary: str = "",
    details: dict | None = None,
) -> AuditLog:
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        summary=summary[:300],
        details=details or {},
    )
