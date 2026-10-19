from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, PurchaseReceiptLine, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "contact_person", "phone", "email", "active")
    list_filter = ("active",)
    search_fields = ("code", "name_ar", "name_en", "contact_person", "phone", "email")


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0
    can_delete = False
    readonly_fields = (
        "material", "quantity", "unit", "quantity_base", "unit_price",
        "price_per_base_unit", "line_total", "sequence", "notes",
    )


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "supplier", "order_date", "expected_date", "status", "created_by")
    list_filter = ("status", "supplier")
    search_fields = ("order_number", "supplier_reference", "supplier__code", "supplier__name_ar")
    readonly_fields = tuple(field.name for field in PurchaseOrder._meta.fields)
    inlines = [PurchaseOrderLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active


@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(admin.ModelAdmin):
    list_display = ("order", "material", "quantity", "unit", "unit_price", "line_total", "sequence")
    list_select_related = ("order", "material", "unit")
    search_fields = ("order__order_number", "material__sku", "material__name_ar")
    readonly_fields = tuple(field.name for field in PurchaseOrderLine._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active


class PurchaseReceiptLineInline(admin.TabularInline):
    model = PurchaseReceiptLine
    extra = 0
    can_delete = False
    readonly_fields = (
        "order_line", "movement", "quantity", "unit", "quantity_base",
        "unit_price", "price_per_base_unit", "line_total", "sequence",
    )


@admin.register(PurchaseReceipt)
class PurchaseReceiptAdmin(admin.ModelAdmin):
    list_display = ("reference", "order", "received_date", "destination_location", "actor", "created_at")
    list_select_related = ("order", "destination_location", "actor")
    search_fields = ("reference", "supplier_reference", "order__order_number", "order__supplier__name_ar")
    readonly_fields = tuple(field.name for field in PurchaseReceipt._meta.fields)
    inlines = [PurchaseReceiptLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active


@admin.register(PurchaseReceiptLine)
class PurchaseReceiptLineAdmin(admin.ModelAdmin):
    list_display = ("receipt", "order_line", "quantity", "unit", "unit_price", "line_total", "sequence")
    list_select_related = ("receipt", "order_line", "unit")
    search_fields = ("receipt__reference", "order_line__material__sku")
    readonly_fields = tuple(field.name for field in PurchaseReceiptLine._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active
