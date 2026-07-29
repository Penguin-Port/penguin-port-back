import uuid

from django.db import models

from stores.models import Store


class ProductCategory(models.Model):
    class Kind(models.TextChoices):
        DRINK = "DRINK", "Drink"
        FOOD = "FOOD", "Food"
        DESSERT = "DESSERT", "Dessert"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=Kind.choices)


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(
        ProductCategory, on_delete=models.PROTECT, related_name="products"
    )
    name = models.CharField(max_length=120)
    price = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
