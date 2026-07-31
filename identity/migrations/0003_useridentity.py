import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_existing_user_identities(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    user_identity = apps.get_model("identity", "UserIdentity")
    database_alias = schema_editor.connection.alias
    user_identity.objects.using(database_alias).bulk_create(
        [
            user_identity(user_id=user_id)
            for user_id in user_model.objects.values_list("id", flat=True)
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0002_refreshtokensession"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserIdentity",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_identity",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.RunPython(
            create_existing_user_identities,
            migrations.RunPython.noop,
        ),
    ]
