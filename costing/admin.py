from django.contrib import admin

from .models import (
    CostingPeriod,
    MonthlyExpense,
    MonthlyProfitCOGSLine,
    MonthlyProfitPeriod,
    ProductCostingEntry,
    ProductSellingPrice,
    RawMaterialPrice,
    RollYieldStandard,
)


@admin.register(RawMaterialPrice)
class RawMaterialPriceAdmin(admin.ModelAdmin):
    list_display = ("material", "price_per_unit", "unit", "price_per_base_unit", "effective_date", "created_by")
    list_filter = ("effective_date",)
    search_fields = ("material__sku", "material__name_ar")


@admin.register(RollYieldStandard)
class RollYieldStandardAdmin(admin.ModelAdmin):
    list_display = ("product", "raw_material", "production_line", "expected_cartons_per_roll", "active")
    list_filter = ("active", "production_line")
    search_fields = ("product__sku", "raw_material__sku")


@admin.register(MonthlyExpense)
class MonthlyExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "year",
        "month",
        "expense_type",
        "scope",
        "amount",
        "status",
        "expense_date",
        "payment_date",
        "description",
        "production_line",
        "product",
    )
    list_filter = ("year", "month", "expense_type", "scope", "status")
    search_fields = ("description", "reference")
    readonly_fields = (
        "status",
        "reversed_at",
        "reversed_by",
        "reversal_reason",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    )

    # The admin is an alternate entry point into the same data as the expense
    # views, so it enforces the same explicit permission. Staff status or the
    # default model permissions alone are not enough to create, edit, or
    # remove a monthly expense. (Reversal is the documented removal path; a
    # hard delete is kept only for users holding manage_expenses.)
    def _manage_expenses(self, request):
        return request.user.has_perm("costing.manage_expenses")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and self._manage_expenses(request)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self._manage_expenses(request)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and self._manage_expenses(request)


class ProductCostingEntryInline(admin.TabularInline):
    model = ProductCostingEntry
    extra = 0
    can_delete = False
    readonly_fields = (
        "product",
        "production_line",
        "good_units_produced",
        "actual_material_cost",
        "allocated_labor_cost",
        "allocated_electricity_cost",
        "allocated_water_cost",
        "allocated_other_cost",
        "total_production_cost",
        "cost_per_good_unit",
        "material_cost_per_good_unit",
        "calculation_status",
    )


# Derived/lifecycle fields are never hand-edited in admin; they are set by the
# costing service when a period is calculated, finalized, or reopened.
COSTING_PERIOD_COMPUTED_FIELDS = (
    "status",
    "material_valuation_method",
    "materials_total",
    "labor_total",
    "electricity_total",
    "water_total",
    "other_industrial_total",
    "total_production_cost",
    "is_complete",
    "missing_data_notes",
    "calculated_at",
    "calculated_by",
    "finalized_at",
    "finalized_by",
    "reopened_at",
    "reopened_by",
    "reopen_reason",
)


@admin.register(CostingPeriod)
class CostingPeriodAdmin(admin.ModelAdmin):
    list_display = ("year", "month", "status", "allocation_policy", "water_total", "total_production_cost", "is_complete", "calculated_at")
    list_filter = ("status", "year", "allocation_policy")
    readonly_fields = COSTING_PERIOD_COMPUTED_FIELDS
    inlines = [ProductCostingEntryInline]

    def get_readonly_fields(self, request, obj=None):
        # A finalized period is fully read-only: reopening requires an explicit
        # service action with a recorded reason, not an admin field edit.
        if obj is not None and obj.status == CostingPeriod.Status.FINALIZED:
            return [field.name for field in self.model._meta.fields]
        return self.readonly_fields


@admin.register(ProductSellingPrice)
class ProductSellingPriceAdmin(admin.ModelAdmin):
    list_display = ("product", "selling_price", "unit", "updated_at", "updated_by")
    search_fields = ("product__sku", "product__name_ar")


class MonthlyProfitCOGSLineInline(admin.TabularInline):
    model = MonthlyProfitCOGSLine
    extra = 0
    can_delete = False
    readonly_fields = (
        "product",
        "quantity_sold_base",
        "base_uom_code",
        "base_uom_name_ar",
        "revenue",
        "cost_per_base_unit",
        "cogs",
        "shipment_count",
        "source_costing_entry",
        "source_period",
        "calculation_status",
        "shipment_references",
        "notes",
    )


@admin.register(MonthlyProfitPeriod)
class MonthlyProfitPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "year",
        "month",
        "status",
        "revenue",
        "cogs",
        "gross_profit",
        "operating_expenses_total",
        "monthly_result",
        "closing_accumulated_loss",
        "profit_after_loss_recovery",
        "is_complete",
    )
    list_filter = ("status", "year", "is_complete")
    readonly_fields = [field.name for field in MonthlyProfitPeriod._meta.fields]
    inlines = [MonthlyProfitCOGSLineInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return bool(obj and obj.status != MonthlyProfitPeriod.Status.FINALIZED)
