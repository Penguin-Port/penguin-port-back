from django.contrib import admin

from identity.models import RefreshTokenSession, VerificationChallenge


admin.site.register([VerificationChallenge, RefreshTokenSession])
