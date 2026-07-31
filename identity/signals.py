from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from identity.models import UserIdentity


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_identity(sender, instance, created, **kwargs):
    if created:
        UserIdentity.objects.get_or_create(user=instance)
