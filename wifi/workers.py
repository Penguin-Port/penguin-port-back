from django.db import transaction
from django.utils import timezone

from operations.models import Notification
from operations.services import emit_event, send_demo_notification
from wifi.adapters import get_network_adapter
from wifi.models import ScheduledAction, WiFiPass


@transaction.atomic
def execute_scheduled_action(action_id, *, now=None):
    now = now or timezone.now()
    action = (
        ScheduledAction.objects.select_for_update()
        .select_related("wifi_pass", "wifi_pass__store")
        .get(id=action_id)
    )
    if action.completed_at is not None:
        return "ALREADY_COMPLETED"
    action.attempts += 1
    wifi_pass = action.wifi_pass
    if action.pass_version != wifi_pass.pass_version:
        action.completed_at = now
        action.save(update_fields=["attempts", "completed_at"])
        return "STALE_VERSION_SKIPPED"
    if action.execute_at > now:
        action.save(update_fields=["attempts"])
        return "NOT_DUE"

    if action.action_type == ScheduledAction.ActionType.EXPIRE_PASS:
        if wifi_pass.expires_at <= now and wifi_pass.status not in [
            WiFiPass.Status.EXPIRED,
            WiFiPass.Status.CANCELLED,
            WiFiPass.Status.BLOCKED,
        ]:
            wifi_pass.status = WiFiPass.Status.EXPIRED
            wifi_pass.save(update_fields=["status"])
            emit_event(
                store=wifi_pass.store,
                type="wifi.pass.expired",
                aggregate_type="WiFiPass",
                aggregate_id=wifi_pass.id,
                payload={"passId": str(wifi_pass.id)},
            )
    elif action.action_type == ScheduledAction.ActionType.SEND_EXPIRING_NOTIFICATION:
        send_demo_notification(
            store=wifi_pass.store,
            channel=Notification.Channel.IN_APP,
            template="WIFI_EXPIRING",
            destination_last4="",
            payload={"passId": str(wifi_pass.id), "expiresAt": wifi_pass.expires_at.isoformat()},
        )
    elif action.action_type == ScheduledAction.ActionType.REVOKE_NETWORK_ACCESS:
        get_network_adapter().revoke(reference=wifi_pass.network_reference or "")
        wifi_pass.network_reference = ""
        wifi_pass.save(update_fields=["network_reference"])

    action.completed_at = now
    action.save(update_fields=["attempts", "completed_at"])
    return "COMPLETED"


@transaction.atomic
def _expire_due_pass(pass_id, *, now):
    """만료 루프에서 한 이용권을 잠그고 상태를 다시 확인한 뒤 종료한다."""

    wifi_pass = (
        WiFiPass.objects.select_for_update()
        .select_related("store")
        .get(id=pass_id)
    )
    if wifi_pass.status not in [WiFiPass.Status.ACTIVE, WiFiPass.Status.EXPIRING_SOON]:
        return False
    if wifi_pass.expires_at > now:
        return False

    get_network_adapter().revoke(reference=wifi_pass.network_reference or "")
    wifi_pass.status = WiFiPass.Status.EXPIRED
    wifi_pass.network_reference = ""
    wifi_pass.pass_version += 1
    wifi_pass.save(update_fields=["status", "network_reference", "pass_version"])
    emit_event(
        store=wifi_pass.store,
        type="wifi.pass.expired",
        aggregate_type="WiFiPass",
        aggregate_id=wifi_pass.id,
        payload={"passId": str(wifi_pass.id), "source": "expire_loop"},
    )
    return True


def expire_due_passes(*, now=None, limit=500):
    """PDF MVP의 60초 만료 루프가 호출할 직접 스캔 구현."""

    now = now or timezone.now()
    pass_ids = list(
        WiFiPass.objects.filter(
            status__in=[WiFiPass.Status.ACTIVE, WiFiPass.Status.EXPIRING_SOON],
            expires_at__lte=now,
        )
        .order_by("expires_at")
        .values_list("id", flat=True)[:limit]
    )
    expired = 0
    for pass_id in pass_ids:
        if _expire_due_pass(pass_id, now=now):
            expired += 1
    return expired


def process_due_actions(*, now=None, limit=500):
    now = now or timezone.now()
    ids = list(
        ScheduledAction.objects.filter(completed_at__isnull=True, execute_at__lte=now)
        .order_by("execute_at")
        .values_list("id", flat=True)[:limit]
    )
    return [execute_scheduled_action(action_id, now=now) for action_id in ids]
