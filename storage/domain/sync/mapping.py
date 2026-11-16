"""Чистые функции сериализации для S3 Data Lake (ГИП-Ассистент).

Ноль I/O, ноль ORM-запросов. Объекты приходят со сделанным select_related.
Детерминизм: одинаковые входы → идентичные байты.
"""

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone as dt_timezone

from django.utils import timezone

# Литерал uuid5 для неймспейса (зафиксирован, не пересчитывается).
S3_SYNC_NAMESPACE = uuid.UUID("b1ae1443-9dc2-5102-90d2-cbc961a334d2")
SCHEMA_VERSION = "1.0"
SOURCE_SYSTEM = "stream-cad"
TASK_SOURCE = "stream_cad"

# Маппинг DesignTask.status → TaskStatus ГИП-Ассистента (9→6, верхний регистр).
TASK_STATUS_MAP = {
    "DRAFT": "NOT_STARTED",
    "ISSUED": "NOT_STARTED",
    "IN_PROGRESS": "IN_PROGRESS",
    "ON_HOLD": "NOT_STARTED",
    "REVIEW": "REVIEW",
    "RETURNED": "REWORK",
    "COMPLETED": "DONE",
    "ACCEPTED": "DONE",
    "CANCELLED": "CANCELLED",
}

GA_OPEN_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "REVIEW", "REWORK"}

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


# ─── UUID5 ───────────────────────────────────────────────────────────────────


def uuid_for_project(project_code: str) -> str:
    return str(uuid.uuid5(S3_SYNC_NAMESPACE, f"project:{project_code.strip()}"))


def uuid_for_design_task(project_code: str, task_pk: int) -> str:
    return str(uuid.uuid5(S3_SYNC_NAMESPACE, f"design-task:{project_code.strip()}:{int(task_pk)}"))


def uuid_for_rfi(project_code: str, rfi_pk: int) -> str:
    return str(uuid.uuid5(S3_SYNC_NAMESPACE, f"rfi:{project_code.strip()}:{int(rfi_pk)}"))


# ─── Slug ─────────────────────────────────────────────────────────────────────


def project_slug(project_code: str) -> str:
    code = project_code.strip()
    slug = _SLUG_UNSAFE.sub("_", code).strip("._-")
    if slug == code and slug:
        return slug[:100]
    suffix = uuid.uuid5(S3_SYNC_NAMESPACE, f"slug:{code}").hex[:8]
    return f"{slug[:91]}-u{suffix}"


# ─── Маппинг статусов ────────────────────────────────────────────────────────


def map_task_status(status: str) -> str:
    try:
        return TASK_STATUS_MAP[status]
    except KeyError:
        raise ValueError(f"map_task_status: неизвестный статус DesignTask: {status!r}")


# ─── Время ────────────────────────────────────────────────────────────────────


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")


def date_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def full_name(user) -> str | None:
    if user is None:
        return None
    return user.get_full_name() or user.username


# ─── Дедлайн-метрики ──────────────────────────────────────────────────────────


def compute_deadline_metrics(
    due_date: date | None, ga_status: str, today: date,
) -> tuple[int, int | None]:
    if due_date is None:
        return 0, None
    if ga_status not in GA_OPEN_STATUSES:
        return 0, None
    days_until = (due_date - today).days
    return max(0, -days_until), days_until


# ─── Сериализация DesignTask ──────────────────────────────────────────────────


def serialize_design_task(task, *, today=None) -> dict:
    code = task.project.code
    if today is None:
        today = timezone.localdate()
    ga_status = map_task_status(task.status)
    overdue_days, days_until = compute_deadline_metrics(task.due_date, ga_status, today)
    return {
        "id": uuid_for_design_task(code, task.pk),
        "project_id": uuid_for_project(code),
        "title": task.title,
        "description": task.description,
        "status": ga_status,
        "assignee_id": None,
        "deadline": date_iso(task.due_date),
        "section_code": task.source_section.code,
        "to_section_code": task.target_section.code,
        "sort_order": 0,
        "source": TASK_SOURCE,
        "is_complex": False,
        "decision_id": None,
        "is_rfi": False,
        "rfi_cause_decision_id": None,
        "rfi_cause_goal_id": None,
        "rfi_due_date": None,
        "rfi_state": None,
        "premise_ids": [],
        "created_at": iso_utc(task.created_at),
        "updated_at": iso_utc(task.updated_at),
        "stream_cad_id": task.pk,
        "stream_cad_status": task.status,
        "stream_cad_priority": task.priority,
        "assignee_name": full_name(task.assignee),
        "overdue_days": overdue_days,
        "days_until_deadline": days_until,
    }


# ─── Сериализация Section ────────────────────────────────────────────────────


def serialize_section(section) -> dict:
    return {
        "code": section.code,
        "name": section.name,
        "full_code": section.full_code or None,
    }


# ─── Сериализатор JSON ────────────────────────────────────────────────────────


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()