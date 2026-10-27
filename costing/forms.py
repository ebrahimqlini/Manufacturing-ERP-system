from decimal import Decimal
import uuid

from django import forms

from catalog.models import Product, UnitOfMeasure
from manufacturing.models import ProductionLine
from .models import (
    CostingPeriod,
    MonthlyExpense,
    ProductSellingPrice,
    RawMaterialPrice,
    RollYieldStandard,
)


class ArabicFormMixin:
    def apply_arabic_widgets(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


MONTH_CHOICES = [
    (1, "1 — يناير"),
    (2, "2 — فبراير"),
    (3, "3 — مارس"),
    (4, "4 — أبريل"),
    (5, "5 — مايو"),
    (6, "6 — يونيو"),
    (7, "7 — يوليو"),
    (8, "8 — أغسطس"),
    (9, "9 — سبتمبر"),
    (10, "10 — أكتوبر"),
    (11, "11 — نوفمبر"),
    (12, "12 — ديسمبر"),
]


class RawMaterialPriceForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = RawMaterialPrice
        fields = ("material", "price_per_unit", "unit", "effective_date", "notes")
        widgets = {
            "effective_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()


class RollYieldStandardForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = RollYieldStandard
        fields = (
            "raw_material",
            "product",
            "production_line",
            "expected_cartons_per_roll",
            "roll_unit",
            "carton_unit",
            "supplier_name",
            "specification_reference",
            "revision",
            "effective_date",
            "target_yield_percent",
            "max_scrap_percent",
            "active",
            "notes",
        )
        widgets = {
            "effective_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["raw_material"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["production_line"].queryset = ProductionLine.objects.filter(active=True)
        self.fields["roll_unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.fields["carton_unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()


class MonthlyExpenseForm(ArabicFormMixin, forms.ModelForm):
    month = forms.TypedChoiceField(label="الشهر", choices=MONTH_CHOICES, coerce=int)
    # A stable idempotency token travels with the rendered form. Repeating the
    # same submission (double click, browser retry) cannot record the expense
    # twice, while a fresh form gets a fresh token for a genuinely new expense.
    submission_key = forms.UUIDField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = MonthlyExpense
        fields = (
            "year",
            "month",
            "expense_type",
            "scope",
            "production_line",
            "product",
            "amount",
            "expense_date",
            "payment_date",
            "reference",
            "description",
            "notes",
        )
        widgets = {
            "expense_date": forms.DateInput(attrs={"type": "date"}),
            "payment_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["production_line"].queryset = ProductionLine.objects.filter(active=True)
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        # Zero is a valid, meaningful amount (a confirmed no-cost month) and must
        # be distinguishable from a month where the bill was simply not recorded.
        self.fields["amount"].widget.attrs["min"] = "0"
        self.fields["amount"].widget.attrs["step"] = "0.01"
        if self.instance.pk:
            self.fields["submission_key"].disabled = True
        self.apply_arabic_widgets()

    def clean_submission_key(self):
        return self.cleaned_data.get("submission_key") or uuid.uuid4()


class CostingPeriodPolicyForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = CostingPeriod
        fields = ("allocation_policy", "waste_policy")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class ReopenPeriodForm(ArabicFormMixin, forms.Form):
    reason = forms.CharField(
        label="سبب إعادة فتح الفترة المعتمدة",
        widget=forms.Textarea(attrs={"rows": 3}),
        required=True,
        help_text="يتم حفظ هذا السبب في سجل التدقيق غير القابل للحذف.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class ProfitPeriodReopenForm(ArabicFormMixin, forms.Form):
    reason = forms.CharField(
        label="سبب إعادة فتح نتيجة الشهر",
        widget=forms.Textarea(attrs={"rows": 3}),
        required=True,
        help_text="يحفظ النظام السبب والمستخدم والوقت في سجل التدقيق.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class ExpenseReverseForm(ArabicFormMixin, forms.Form):
    reason = forms.CharField(
        label="سبب عكس المصروف",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=True,
        help_text="لا يحذف النظام المصروف؛ يحفظه بحالة معكوس مع سبب العكس.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class ProductSellingPriceForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = ProductSellingPrice
        fields = ("product", "selling_price", "unit", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()
