import uuid
from datetime import time

from django.conf import settings
from django.db import models


class Store(models.Model):
    class Segment(models.TextChoices):
        UNIVERSITY = "UNIVERSITY", "대학가"
        FRANCHISE = "FRANCHISE", "프랜차이즈"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    timezone = models.CharField(max_length=64, default="Asia/Seoul")
    business_day_cutoff = models.TimeField(default=time(0, 0))
    segment = models.CharField(
        max_length=20, choices=Segment.choices, default=Segment.UNIVERSITY
    )
    otp_skip_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class StoreMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        MANAGER = "MANAGER", "Manager"
        STAFF = "STAFF", "Staff"
        VIEWER = "VIEWER", "Viewer"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="store_memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "user"], name="unique_store_user_membership"
            )
        ]
