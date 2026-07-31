from django.contrib import admin

from wifi.models import (
    PassExtension,
    ScheduledAction,
    WiFiAmountTier,
    WiFiPass,
    WiFiPolicy,
)


admin.site.register(
    [WiFiPolicy, WiFiAmountTier, WiFiPass, PassExtension, ScheduledAction]
)
