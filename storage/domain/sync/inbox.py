"""Inbox — разбор управляющих сигналов из S3 (ГИП-Ассистент).

Алгоритм (раздел 3.5.1-3.5.3 плана):
1. Читаем все ключи в projects/<slug>/inbox/commands/<seq>_<type>_<command_id>.json
2. Select-проекта нет: парсим ключ, извлекаем project_code, seq, command_id
3. Каждый файл — одна команда; обрабатываются по seq.
4. Успех → move в done/<seq>_<command_id>.json, обновляем last_inbox_seq
5. Ошибка → move в failed/<seq>_<command_id>.json
"""

import json
import logging
import os
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from storage.adapters.s3_boto3 import S3Boto3Gateway
from storage.domain.models import InboxCommand, Project, SyncLog, SyncState
from storage.domain.sync.export import get_s3_gateway
from storage.domain.sync.mapping import project_slug, sha256_hex

logger = logging.getLogger(__name__)

# Паттерн для разбора ключа inbox-команды:
# projects/<slug>/inbox/commands/<seq>_<type>_<command_id>.json
_INBOX_KEY_RE = re.compile(
    r"^projects/(?P<slug>[^/]+)/inbox/commands/(?P<seq>\d+)_(?P<type>[\w.]+)_(?P<cid>[^/]+)\.json$"
)

# Allowlist типов команд.
_ALLOWED_TYPES = frozenset({
    "ack.import",
    "rfi.escalate",
    "rfi.proposal",
    "task.deadline_update",
})


def _project_from_slug(slug: str) -> Project | None:
    return Project.objects.filter(
        is_deleted=False,
    ).extra(
        where=["1=1"],
    ).filter(
        # project_slug детерминирован — вычисляем все и ищем совпадение
        code__in=list(Project.objects.filter(is_deleted=False).values_list("code", flat=True)),
    ).first()


def process_project_inbox(project: Project) -> int:
    """Разбор inbox-команд для одного проекта.

    Возвращает количество обработанных команд.
    """
    if not settings.S3_SYNC_ENABLED:
        return 0

    gateway = get_s3_gateway()
    slug = project_slug(project.code)
    prefix = f"projects/{slug}/inbox/commands/"
    sync_state, _ = SyncState.objects.get_or_create(project=project)
    last_seq = sync_state.last_inbox_seq
    keys = sorted(gateway.list_keys(prefix))

    count = 0
    for key in keys:
        m = _INBOX_KEY_RE.match(key)
        if not m:
            continue
        seq = int(m.group("seq"))
        if seq <= last_seq:
            continue

        cmd_type = m.group("type")
        command_id = m.group("cid")
        if cmd_type not in _ALLOWED_TYPES:
            logger.warning("inbox[%s]: unknown type %s, moving to failed", project.code, cmd_type)
            _move_to_failed(gateway, slug, seq, command_id, cmd_type, "unknown type")
            continue

        raw = gateway.get_json_object(key)
        if raw is None:
            continue
        payload = raw.get("payload") or {}

        # Идемпотентность по command_id
        if InboxCommand.objects.filter(project=project, command_id=command_id).exists():
            last_seq = seq
            sync_state.last_inbox_seq = seq
            sync_state.save(update_fields=["last_inbox_seq"])
            continue

        try:
            with transaction.atomic():
                cmd = InboxCommand.objects.create(
                    project=project, command_id=command_id,
                    seq=seq, type=cmd_type, payload=payload,
                    status=InboxCommand.Status.IN_PROGRESS,
                    file_key=key,
                )
                _apply_command(project, cmd)
                cmd.status = InboxCommand.Status.DONE
                cmd.processed_at = timezone.now()
                cmd.save(update_fields=["status", "processed_at"])
                sync_state.last_inbox_seq = seq
                sync_state.save(update_fields=["last_inbox_seq"])
                SyncLog.objects.create(
                    project=project, direction="in", level="info",
                    generation=sync_state.generation,
                    message=f"inbox {cmd_type} processed",
                    detail={"command_id": command_id, "seq": seq},
                )
                gateway.move_object(
                    key,
                    f"projects/{slug}/inbox/done/{seq}_{cmd_type}_{command_id}.json",
                )
                count += 1
        except Exception as exc:
            logger.exception("inbox[%s] command %s failed: %s", project.code, command_id, exc)
            InboxCommand.objects.filter(
                project=project, command_id=command_id,
            ).update(status=InboxCommand.Status.FAILED, error=str(exc))
            SyncLog.objects.create(
                project=project, direction="in", level="error",
                generation=sync_state.generation,
                message=f"inbox {cmd_type} failed: {exc}",
                detail={"command_id": command_id, "seq": seq, "error": str(exc)},
            )
            gateway.move_object(
                key,
                f"projects/{slug}/inbox/failed/{seq}_{cmd_type}_{command_id}.json",
            )
    return count


def _apply_command(project, cmd: InboxCommand) -> None:
    """Применяет allowlist-команду. Исключения не глотаются."""
    payload = cmd.payload or {}
    if cmd.type == "ack.import":
        gen = payload.get("generation", 0)
        SyncState.objects.filter(project=project).update(
            last_ack_generation=gen,
            last_ack_at=timezone.now(),
        )
        return
    if cmd.type == "rfi.escalate":
        _apply_rfi_escalate(project, payload)
        return
    if cmd.type == "rfi.proposal":
        logger.info("inbox[%s] rfi.proposal: %s", project.code, payload)
        return
    if cmd.type == "task.deadline_update":
        logger.info("inbox[%s] task.deadline_update: %s (pending approval)", project.code, payload)
        return


def _apply_rfi_escalate(project, payload: dict) -> None:
    """Эскалирует RFI по inbox-команде (если переход разрешён матрицей)."""
    from storage.domain.models import RfiTask
    from storage.domain.services import _rfi_check_can_change, escalate_rfi

    number = payload.get("rfi_number")
    if number is None:
        raise ValueError("rfi.escalate: missing rfi_number")
    rfi = RfiTask.objects.filter(project=project, number=number).first()
    if rfi is None:
        raise ValueError(f"rfi.escalate: RFI-{number:02d} not found")
    if rfi.status not in ("sent", "answered"):
        raise ValueError(f"rfi.escalate: RFI-{number:02d} status {rfi.status} cannot be escalated")
    actor = project.gip or rfi.assignee
    if actor is None:
        raise ValueError("rfi.escalate: no gip or assignee to act")
    escalate_rfi(actor, rfi, comment="Эскалация по запросу ГИП-Ассистента")


def _move_to_failed(gateway, slug, seq, cid, cmd_type, reason):
    dst = f"projects/{slug}/inbox/failed/{seq}_{cmd_type}_{cid}.json"
    src = f"projects/{slug}/inbox/commands/{seq}_{cmd_type}_{cid}.json"
    try:
        gateway.move_object(src, dst)
    except Exception:
        pass


# ─── Фасад для всех проектов ─────────────────────────────────────────────────


def process_all_inboxes() -> int:
    """Разбор inbox для всех enabled проектов. Celery-задача."""
    total = 0
    for state in SyncState.objects.filter(enabled=True).select_related("project"):
        try:
            total += process_project_inbox(state.project)
        except Exception as exc:
            logger.exception("process_all_inboxes[%s]: %s", state.project.code, exc)
    return total