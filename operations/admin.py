from django.contrib import admin

from operations.models import (
    AuditLog,
    DemoMessage,
    Notification,
    OutboxEvent,
    PrivacyRetentionPolicy,
)


admin.site.register(
    [Notification, DemoMessage, AuditLog, PrivacyRetentionPolicy, OutboxEvent]
)
