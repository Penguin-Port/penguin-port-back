from django.contrib import admin

from operations.models import (
    AuditLog,
    Notification,
    OutboxEvent,
    PrivacyRetentionPolicy,
)


admin.site.register([Notification, AuditLog, PrivacyRetentionPolicy, OutboxEvent])
