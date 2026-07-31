from django.contrib import admin

from catalog.models import Product, ProductCategory


admin.site.register([ProductCategory, Product])
