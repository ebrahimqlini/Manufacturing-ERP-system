from django.contrib import admin

from .models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "dimension", "active")
    search_fields = ("code", "name_ar", "name_en", "dimension")
    list_filter = ("dimension", "active")


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "active")
    search_fields = ("code", "name_ar", "name_en")
    list_filter = ("active",)


class ProductUnitConversionInline(admin.TabularInline):
    model = ProductUnitConversion
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name_ar", "category", "base_uom", "active")
    search_fields = ("sku", "name_ar", "name_en")
    list_filter = ("category", "active")
    inlines = (ProductUnitConversionInline,)
    readonly_fields = ("created_at", "updated_at")
