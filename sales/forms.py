from datetime import date
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import inlineformset_factory

from catalog.models import Product, UnitOfMeasure
from inventory.models import StorageLocation
from inventory.services import to_base_quantity

from .models import Customer, SalesOrder, SalesOrderLine


class ArabicFormMixin:
    def apply_arabic_widgets(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class CustomerForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = Customer
        fields = ("code", "name_ar", "name_en", "contact_person", "phone", "email", "address", "notes", "active")
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if not code:
            raise ValidationError("رمز العميل مطلوب.")
        return code


class SalesOrderForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = SalesOrder
        fields = ("customer", "order_date", "requested_delivery_date", "customer_reference", "notes")
        widgets = {
            "order_date": forms.DateInput(attrs={"type": "date"}),
            "requested_delivery_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        customers = Customer.objects.filter(active=True)
        if self.instance.pk and self.instance.customer_id:
            customers = Customer.objects.filter(Q(active=True) | Q(pk=self.instance.customer_id))
        self.fields["customer"].queryset = customers
        self.apply_arabic_widgets()


class SalesOrderLineForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = SalesOrderLine
        fields = ("product", "quantity", "unit", "unit_price", "notes")
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.0001", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        quantity = cleaned.get("quantity")
        unit = cleaned.get("unit")
        if product and quantity and unit:
            try:
                to_base_quantity(product, quantity, unit)
            except ValidationError as exc:
                self.add_error("unit", exc)
        return cleaned


class BaseSalesOrderLineFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        kept = 0
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE"):
                continue
            if data.get("product"):
                kept += 1
        if kept == 0:
            raise ValidationError("أضف بندًا واحدًا على الأقل لأمر البيع.")


SalesOrderLineFormSet = inlineformset_factory(
    SalesOrder,
    SalesOrderLine,
    form=SalesOrderLineForm,
    formset=BaseSalesOrderLineFormSet,
    extra=3,
    can_delete=True,
)


class ShipmentForm(ArabicFormMixin, forms.Form):
    reference = forms.CharField(label="مرجع الشحن الداخلي (فريد)", max_length=100)
    customer_reference = forms.CharField(
        label="مرجع إذن/فاتورة العميل", max_length=120, required=False
    )
    shipment_date = forms.DateField(
        label="تاريخ الشحن", initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    source_location = forms.ModelChoiceField(
        label="موقع الصرف", queryset=StorageLocation.objects.none()
    )
    notes = forms.CharField(
        label="ملاحظات", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, order, **kwargs):
        self.order = order
        super().__init__(*args, **kwargs)
        self.fields["source_location"].queryset = StorageLocation.objects.filter(
            active=True, warehouse__active=True
        ).select_related("warehouse")
        self._outstanding_order_lines = []
        self.outstanding_rows = []
        for line in order.lines.select_related("product", "product__base_uom", "unit").order_by("sequence", "pk"):
            outstanding = line.outstanding_quantity_base
            if outstanding <= 0:
                continue
            quantity_name = f"quantity_{line.pk}"
            unit_name = f"unit_{line.pk}"
            self.fields[quantity_name] = forms.DecimalField(
                label=f"كمية {line.product.name_ar}",
                max_digits=30,
                decimal_places=12,
                min_value=Decimal("0"),
                required=False,
                widget=forms.NumberInput(attrs={"class": "form-control", "step": "any", "min": "0"}),
            )
            self.fields[unit_name] = forms.ModelChoiceField(
                label=f"وحدة {line.product.name_ar}",
                queryset=UnitOfMeasure.objects.filter(active=True),
                initial=line.unit,
                required=False,
                widget=forms.Select(attrs={"class": "form-select"}),
            )
            self._outstanding_order_lines.append(line)
        self.apply_arabic_widgets()
        self.outstanding_rows = [
            {
                "order_line": line,
                "quantity_field": self[f"quantity_{line.pk}"],
                "unit_field": self[f"unit_{line.pk}"],
            }
            for line in self._outstanding_order_lines
        ]

    def clean(self):
        cleaned = super().clean()
        lines = []
        for line in self._outstanding_order_lines:
            quantity = cleaned.get(f"quantity_{line.pk}")
            unit = cleaned.get(f"unit_{line.pk}")
            if quantity is None or quantity == 0:
                continue
            if unit is None:
                self.add_error(f"unit_{line.pk}", "اختر وحدة الشحن.")
                continue
            try:
                quantity_base = to_base_quantity(line.product, quantity, unit)
            except ValidationError as exc:
                self.add_error(f"unit_{line.pk}", exc)
                continue
            if quantity_base > line.outstanding_quantity_base:
                self.add_error(
                    f"quantity_{line.pk}",
                    f"الكمية تتجاوز المتبقي لهذا البند ({line.outstanding_quantity_base}).",
                )
                continue
            lines.append({"order_line": line, "quantity": quantity, "unit": unit})
        if not lines and not self.errors:
            self.add_error(None, "أدخل كمية مشحونة واحدة على الأقل.")
        cleaned["lines"] = lines
        return cleaned
