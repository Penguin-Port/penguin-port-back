from django.contrib import admin

from identity.models import RefreshTokenSession, UserIdentity, VerificationChallenge


admin.site.register([VerificationChallenge, RefreshTokenSession, UserIdentity])
