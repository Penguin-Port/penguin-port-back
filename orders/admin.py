from django.contrib import admin

from orders.models import IdempotencyRecord, Order, OrderClaim, OrderItem


admin.site.register([Order, OrderItem, OrderClaim, IdempotencyRecord])
