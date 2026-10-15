from django import forms

from catalog.models import Product, UnitOfMeasure
from .models import StorageLocation, Warehouse


class ArabicModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class WarehouseForm(ArabicModelForm):
    class Meta:
        model = Warehouse
        fields = ("code", "name_ar", "name_en", "active")


class StorageLocationForm(ArabicModelForm):
    class Meta:
        model = StorageLocation
        fields = ("warehouse", "code", "name_ar", "name_en", "active")
        widgets = {"warehouse": forms.Select(attrs={"class": "form-select"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = Warehouse.objects.filter(active=True)


class StockMovementForm(forms.Form):
    reference = forms.CharField(label="مرجع فريد", max_length=100)
    product = forms.ModelChoiceField(label="الصنف", queryset=Product.objects.none())
    quantity = forms.DecimalField(label="الكمية", max_digits=30, decimal_places=12, min_value=0)
    unit = forms.ModelChoiceField(label="وحدة الكمية", queryset=UnitOfMeasure.objects.none())
    source_location = forms.ModelChoiceField(label="من موقع", queryset=StorageLocation.objects.none(), required=False)
    destination_location = forms.ModelChoiceField(label="إلى موقع", queryset=StorageLocation.objects.none(), required=False)
    external_reference = forms.CharField(label="مرجع مستند خارجي", max_length=120, required=False)
    reason = forms.CharField(label="السبب", max_length=240, required=False)
    notes = forms.CharField(label="ملاحظات", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, movement_type, **kwargs):
        self.movement_type = movement_type
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        locations = StorageLocation.objects.filter(active=True, warehouse__active=True).select_related("warehouse")
        self.fields["source_location"].queryset = locations
        self.fields["destination_location"].queryset = locations
        if movement_type in ("OPENING", "RECEIPT", "ADJUSTMENT_IN"):
            self.fields["source_location"].widget = forms.HiddenInput()
            self.fields["destination_location"].required = True
        elif movement_type in ("ISSUE", "ADJUSTMENT_OUT"):
            self.fields["destination_location"].widget = forms.HiddenInput()
            self.fields["source_location"].required = True
        else:
            self.fields["source_location"].required = True
            self.fields["destination_location"].required = True
        if movement_type in ("ADJUSTMENT_IN", "ADJUSTMENT_OUT"):
            self.fields["reason"].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        source, destination = cleaned.get("source_location"), cleaned.get("destination_location")
        if source and destination and source == destination:
            self.add_error("destination_location", "يجب أن يختلف موقع المصدر عن الوجهة.")
        product, unit = cleaned.get("product"), cleaned.get("unit")
        if product and unit and unit.pk != product.base_uom_id:
            if not product.unit_conversions.filter(unit=unit, active=True).exists():
                self.add_error("unit", "لا يوجد تحويل نشط لهذه الوحدة على الصنف.")
            elif unit.dimension != product.base_uom.dimension:
                self.add_error("unit", "لا يمكن التحويل بين وحدات ذات أبعاد مختلفة.")
        return cleaned
