from django.contrib import admin

from stores.models import Store, StoreMembership


admin.site.register([Store, StoreMembership])
