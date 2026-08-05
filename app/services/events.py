from __future__ import annotations

import json
import queue
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BackendEvent
from app.time import aware


_subscribers: dict[str, set[queue.Queue[str]]] = defaultdict(set)
_subscriber_lock = threading.Lock()


def _channel(store_id: str) -> str:
    return f"smartpass:events:{store_id}"


def _event_payload(event: BackendEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "storeId": event.store_id,
        "type": event.event_type,
        "aggregateType": event.aggregate_type,
        "aggregateId": event.aggregate_id,
        "payload": event.payload or {},
        "createdAt": aware(event.created_at).isoformat() if event.created_at else None,
    }


def subscribe(store_id: str) -> queue.Queue[str]:
    subscriber: queue.Queue[str] = queue.Queue(maxsize=100)
    with _subscriber_lock:
        _subscribers[store_id].add(subscriber)
    return subscriber


def unsubscribe(store_id: str, subscriber: queue.Queue[str]) -> None:
    with _subscriber_lock:
        subscribers = _subscribers.get(store_id)
        if subscribers is None:
            return
        subscribers.discard(subscriber)
        if not subscribers:
            _subscribers.pop(store_id, None)


def _broadcast(store_id: str, payload: str) -> None:
    with _subscriber_lock:
        subscribers = tuple(_subscribers.get(store_id, ()))
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(payload)
        except queue.Full:
            # A slow admin client should not block POS/admin requests.
            try:
                subscriber.get_nowait()
                subscriber.put_nowait(payload)
            except queue.Empty:
                pass


def _publish_redis(store_id: str, payload: str) -> None:
    if not settings.redis_url:
        return
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.publish(_channel(store_id), payload)
        client.close()
    except Exception:
        # Redis is an optional cross-process fan-out. The durable DB row and
        # local subscribers still make the endpoint useful without Redis.
        return


def publish_event(
    db: Session,
    *,
    store_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any] | None = None,
) -> BackendEvent:
    event = BackendEvent(
        store_id=store_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload or {},
    )
    db.add(event)
    db.flush()
    serialized = json.dumps(_event_payload(event), ensure_ascii=False)
    _broadcast(store_id, serialized)
    _publish_redis(store_id, serialized)
    return event


def history(
    db: Session,
    *,
    store_id: str,
    last_event_id: str | None = None,
    limit: int = 100,
) -> list[BackendEvent]:
    query = (
        select(BackendEvent)
        .where(BackendEvent.store_id == store_id)
        .order_by(BackendEvent.created_at.asc(), BackendEvent.id.asc())
        .limit(min(max(limit, 1), 500))
    )
    if last_event_id:
        last = db.get(BackendEvent, last_event_id)
        if last and last.store_id == store_id:
            query = query.where(
                (BackendEvent.created_at > last.created_at)
                | (
                    (BackendEvent.created_at == last.created_at)
                    & (BackendEvent.id > last.id)
                )
            )
    return list(db.scalars(query).all())


def sse_frame(payload: dict[str, Any]) -> str:
    data = json.dumps(payload.get("payload", {}), ensure_ascii=False)
    return (
        f"id: {payload['id']}\n"
        f"event: {payload['type']}\n"
        f"data: {data}\n\n"
    )


def stream_events(
    *,
    store_id: str,
    initial: list[BackendEvent],
    last_event_id: str | None = None,
):
    subscriber = subscribe(store_id)
    seen = {event.id for event in initial}
    redis_client = None
    redis_pubsub = None
    if settings.redis_url:
        try:
            import redis

            redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
            redis_pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
            redis_pubsub.subscribe(_channel(store_id))
        except Exception:
            redis_client = None
            redis_pubsub = None

    try:
        for event in initial:
            yield sse_frame(_event_payload(event))
        yield ": connected\n\n"
        deadline = time.monotonic() + settings.sse_max_seconds
        heartbeat_at = time.monotonic() + settings.sse_heartbeat_seconds
        while time.monotonic() < deadline:
            raw_payload = None
            try:
                raw_payload = subscriber.get(timeout=0.5)
            except queue.Empty:
                pass
            if raw_payload is None and redis_pubsub is not None:
                message = redis_pubsub.get_message(timeout=0.01)
                raw_payload = message.get("data") if message else None
            if raw_payload:
                try:
                    payload = json.loads(raw_payload)
                except (TypeError, ValueError):
                    payload = None
                if payload and payload.get("id") not in seen:
                    seen.add(payload["id"])
                    yield sse_frame(payload)
            if time.monotonic() >= heartbeat_at:
                yield ": heartbeat\n\n"
                heartbeat_at = time.monotonic() + settings.sse_heartbeat_seconds
    finally:
        unsubscribe(store_id, subscriber)
        if redis_pubsub is not None:
            try:
                redis_pubsub.close()
            except Exception:
                pass
        if redis_client is not None:
            try:
                redis_client.close()
            except Exception:
                pass

