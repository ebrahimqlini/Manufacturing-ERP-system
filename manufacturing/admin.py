from django.contrib import admin

from .models import (
    BillOfMaterials,
    BOMComponent,
    FinishedGoodsReceipt,
    MachineCounterReading,
    MaterialIssue,
    OperationProgress,
    ProductionLine,
    ProductionMaterialRequirement,
    ProductionOperation,
    ProductionOrder,
    ProductionOrderOperation,
    ProductionResult,
    Routing,
    RoutingStep,
)


class NoDeleteAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductionLine)
class ProductionLineAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name_ar", "active", "counter_behavior")
    search_fields = ("code", "name_ar", "name_en")
    list_filter = ("active", "counter_behavior")


@admin.register(ProductionOperation)
class ProductionOperationAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name_ar", "active")
    search_fields = ("code", "name_ar", "name_en")
    list_filter = ("active",)


class BOMComponentInline(admin.TabularInline):
    model = BOMComponent
    extra = 0
    can_delete = False


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("product", "version", "output_quantity", "output_unit", "active")
    list_filter = ("active",)
    inlines = (BOMComponentInline,)
    readonly_fields = ("created_by", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class RoutingStepInline(admin.TabularInline):
    model = RoutingStep
    extra = 0
    can_delete = False


@admin.register(Routing)
class RoutingAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("product", "production_line", "version", "active")
    list_filter = ("production_line", "active")
    inlines = (RoutingStepInline,)
    readonly_fields = ("created_by", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "product", "production_line", "planned_quantity", "status", "created_at")
    list_filter = ("status", "production_line")
    search_fields = ("order_number", "product__sku", "product__name_ar")
    readonly_fields = tuple(field.name for field in ProductionOrder._meta.fields)

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


class ReadOnlyRecordsAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


for model in (BOMComponent, ProductionMaterialRequirement, ProductionOrderOperation,
              MaterialIssue, OperationProgress, ProductionResult, FinishedGoodsReceipt,
              RoutingStep, MachineCounterReading):
    admin.site.register(model, ReadOnlyRecordsAdmin)
