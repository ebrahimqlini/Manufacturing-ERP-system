from django.contrib import admin

from .models import Customer, SalesOrder, SalesOrderLine, Shipment, ShipmentLine


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "contact_person", "phone", "email", "active")
    list_filter = ("active",)
    search_fields = ("code", "name_ar", "name_en", "contact_person", "phone", "email")


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 0
    can_delete = False
    readonly_fields = (
        "product", "quantity", "unit", "quantity_base", "unit_price",
        "price_per_base_unit", "line_total", "sequence", "notes",
    )


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer", "order_date", "requested_delivery_date", "status", "created_by")
    list_filter = ("status", "customer")
    search_fields = ("order_number", "customer_reference", "customer__code", "customer__name_ar")
    readonly_fields = tuple(field.name for field in SalesOrder._meta.fields)
    inlines = [SalesOrderLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active


@admin.register(SalesOrderLine)
class SalesOrderLineAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "quantity", "unit", "unit_price", "line_total", "sequence")
    list_select_related = ("order", "product", "unit")
    search_fields = ("order__order_number", "product__sku", "product__name_ar")
    readonly_fields = tuple(field.name for field in SalesOrderLine._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active


class ShipmentLineInline(admin.TabularInline):
    model = ShipmentLine
    extra = 0
    can_delete = False
    readonly_fields = (
        "order_line", "movement", "quantity", "unit", "quantity_base",
        "unit_price", "price_per_base_unit", "line_total", "sequence",
    )


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("reference", "order", "shipment_date", "source_location", "actor", "created_at")
    list_select_related = ("order", "source_location", "actor")
    search_fields = ("reference", "customer_reference", "order__order_number", "order__customer__name_ar")
    readonly_fields = tuple(field.name for field in Shipment._meta.fields)
    inlines = [ShipmentLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active


@admin.register(ShipmentLine)
class ShipmentLineAdmin(admin.ModelAdmin):
    list_display = ("shipment", "order_line", "quantity", "unit", "unit_price", "line_total", "sequence")
    list_select_related = ("shipment", "order_line", "unit")
    search_fields = ("shipment__reference", "order_line__product__sku")
    readonly_fields = tuple(field.name for field in ShipmentLine._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active
