from django.contrib import admin

from .models import InventoryLock, StockLedgerEntry, StockMovement, StorageLocation, Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "active")
    search_fields = ("code", "name_ar", "name_en")
    list_filter = ("active",)


@admin.register(StorageLocation)
class StorageLocationAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "code", "name_ar", "active")
    list_filter = ("warehouse", "active")
    search_fields = ("code", "name_ar")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("reference", "movement_type", "product", "quantity", "unit", "occurred_at", "actor")
    list_filter = ("movement_type", "occurred_at")
    search_fields = ("reference", "external_reference", "product__sku", "product__name_ar")
    readonly_fields = tuple(field.name for field in StockMovement._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None): return request.user.is_active


@admin.register(StockLedgerEntry)
class StockLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("movement", "product", "location", "quantity_base", "created_at")
    list_select_related = ("movement", "product", "location")
    readonly_fields = tuple(field.name for field in StockLedgerEntry._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None): return request.user.is_active


@admin.register(InventoryLock)
class InventoryLockAdmin(admin.ModelAdmin):
    list_display = ("product", "location")
    readonly_fields = tuple(field.name for field in InventoryLock._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
