from django.contrib import admin

from inventory.models import InventoryEvent, InventoryItem


admin.site.register([InventoryItem, InventoryEvent])
