from django import forms

from .models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure


class ArabicModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class UnitOfMeasureForm(ArabicModelForm):
    class Meta:
        model = UnitOfMeasure
        fields = ("code", "name_ar", "name_en", "dimension", "active")


class ProductCategoryForm(ArabicModelForm):
    class Meta:
        model = ProductCategory
        fields = ("code", "name_ar", "name_en", "description", "active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class ProductForm(ArabicModelForm):
    class Meta:
        model = Product
        fields = ("sku", "name_ar", "name_en", "description", "category", "base_uom", "active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ProductCategory.objects.filter(active=True)
        self.fields["base_uom"].queryset = UnitOfMeasure.objects.filter(active=True)


class ProductUnitConversionForm(ArabicModelForm):
    class Meta:
        model = ProductUnitConversion
        fields = ("product", "unit", "factor", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
