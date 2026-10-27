from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
import uuid

from catalog.models import Product, UnitOfMeasure
from inventory.models import StorageLocation

from .models import (
    BillOfMaterials,
    BOMComponent,
    MachineCounterReading,
    OperationProgress,
    ProductionLine,
    ProductionOperation,
    ProductionOrder,
    ProductionOrderOperation,
    ProductionResult,
    Routing,
    RoutingStep,
)
from inventory.services import to_base_quantity


class ArabicFormMixin:
    def apply_arabic_widgets(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class ProductionLineForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = ProductionLine
        fields = ("code", "name_ar", "name_en", "active", "counter_behavior")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class ProductionOperationForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = ProductionOperation
        fields = ("code", "name_ar", "name_en", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class BOMForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = BillOfMaterials
        fields = ("product", "version", "output_quantity", "output_unit", "active", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["output_unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()

    def clean(self):
        cleaned = super().clean()
        product, quantity, unit = cleaned.get("product"), cleaned.get("output_quantity"), cleaned.get("output_unit")
        if product and quantity and unit:
            try:
                to_base_quantity(product, quantity, unit)
            except ValidationError as exc:
                self.add_error("output_unit", exc)
        return cleaned


class BOMComponentForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = BOMComponent
        fields = ("material", "quantity", "unit", "sequence", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()

    def clean(self):
        cleaned = super().clean()
        material, quantity, unit = cleaned.get("material"), cleaned.get("quantity"), cleaned.get("unit")
        if material and quantity and unit:
            try:
                to_base_quantity(material, quantity, unit)
            except ValidationError as exc:
                self.add_error("unit", exc)
        return cleaned


BOMComponentFormSet = inlineformset_factory(
    BillOfMaterials, BOMComponent, form=BOMComponentForm, extra=3, can_delete=True,
)


class RoutingForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = Routing
        fields = ("product", "production_line", "version", "active", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True)
        self.fields["production_line"].queryset = ProductionLine.objects.filter(active=True)
        self.apply_arabic_widgets()


class RoutingStepForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = RoutingStep
        fields = ("operation", "sequence", "notes", "is_optional")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import ProductionOperation
        self.fields["operation"].queryset = ProductionOperation.objects.filter(active=True)
        self.apply_arabic_widgets()


RoutingStepFormSet = inlineformset_factory(
    Routing, RoutingStep, form=RoutingStepForm, extra=4, can_delete=True,
)


class ProductionOrderForm(ArabicFormMixin, forms.ModelForm):
    class Meta:
        model = ProductionOrder
        fields = ("product", "production_line", "bom", "routing", "source_location", "planned_quantity", "planned_unit", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True).select_related("base_uom")
        self.fields["production_line"].queryset = ProductionLine.objects.filter(active=True)
        self.fields["bom"].queryset = BillOfMaterials.objects.filter(active=True).select_related("product")
        self.fields["routing"].queryset = Routing.objects.filter(active=True).select_related("product", "production_line")
        self.fields["source_location"].queryset = StorageLocation.objects.filter(active=True, warehouse__active=True).select_related("warehouse")
        self.fields["planned_unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        for field in ("bom", "routing"):
            self.fields[field].required = False
            self.fields[field].help_text = "اختياري في المسودة؛ يلزم ضبطه قبل الإطلاق."
        self.apply_arabic_widgets()


class MaterialIssueForm(ArabicFormMixin, forms.Form):
    reference = forms.CharField(label="مرجع صرف فريد", max_length=100)
    requirement = forms.ModelChoiceField(label="متطلب المادة", queryset=None)
    quantity = forms.DecimalField(label="كمية الصرف الجزئي", max_digits=30, decimal_places=12, min_value=0)
    unit = forms.ModelChoiceField(label="الوحدة", queryset=UnitOfMeasure.objects.none())

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order
        self.fields["requirement"].queryset = order.requirements.select_related("material", "material__base_uom")
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()


class OperationProgressForm(ArabicFormMixin, forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    order_operation = forms.ModelChoiceField(label="عملية المسار", queryset=ProductionOrderOperation.objects.none())
    quantity = forms.DecimalField(label="الكمية المنفذة", max_digits=30, decimal_places=12, min_value=0)
    unit = forms.ModelChoiceField(label="وحدة المنتج", queryset=UnitOfMeasure.objects.none())
    notes = forms.CharField(label="ملاحظات", max_length=240, required=False)

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("submission_key", uuid.uuid4())
        self.fields["order_operation"].queryset = order.operations.exclude(status__in=("COMPLETED", "SKIPPED"))
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()


class ProductionResultForm(ArabicFormMixin, forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    good_quantity = forms.DecimalField(label="الكمية الجيدة", max_digits=30, decimal_places=12, min_value=0, required=False, initial=0)
    scrap_quantity = forms.DecimalField(label="كمية الهالك", max_digits=30, decimal_places=12, min_value=0, required=False, initial=0)
    unit = forms.ModelChoiceField(label="وحدة المنتج", queryset=UnitOfMeasure.objects.none())
    notes = forms.CharField(label="ملاحظات", max_length=240, required=False)

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("submission_key", uuid.uuid4())
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.apply_arabic_widgets()


class ProductionMaterialWasteForm(ArabicFormMixin, forms.Form):
    material = forms.ModelChoiceField(label="المادة الخام التالفة", queryset=Product.objects.none())
    quantity_base = forms.DecimalField(label="الكمية التالفة بالوحدة الأساسية", max_digits=30, decimal_places=12, min_value=0)
    material_issue = forms.ModelChoiceField(label="صرف المادة (اختياري)", queryset=None, required=False)
    roll_reference = forms.CharField(label="مرجع الرول (اختياري)", max_length=120, required=False)

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Product.objects.filter(
            production_requirements__order=order, active=True
        ).distinct().select_related("base_uom")
        self.fields["material"].label_from_instance = lambda material: (
            f"{material.name_ar} ({material.sku}) — الوحدة الأساسية: {material.base_uom.code}"
        )
        self.fields["quantity_base"].help_text = "أدخل الكمية المقاسة بوحدة المادة الأساسية المعروضة أعلاه."
        self.fields["material_issue"].queryset = order.material_issues.filter(
            movement__reversal__isnull=True
        ).select_related("requirement__material")
        self.apply_arabic_widgets()


class ProductionResultCorrectionForm(ArabicFormMixin, forms.Form):
    reason = forms.CharField(label="سبب عكس / تصحيح نتيجة الإنتاج", max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class ProductionWasteReversalForm(ArabicFormMixin, forms.Form):
    reason = forms.CharField(label="سبب عكس سجل هالك المواد", max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_arabic_widgets()


class FinishedGoodsReceiptForm(ArabicFormMixin, forms.Form):
    reference = forms.CharField(label="مرجع استلام فريد", max_length=100)
    quantity = forms.DecimalField(label="الكمية المستلمة", max_digits=30, decimal_places=12, min_value=0)
    unit = forms.ModelChoiceField(label="وحدة المنتج", queryset=UnitOfMeasure.objects.none())
    destination_location = forms.ModelChoiceField(label="موقع استلام المنتج", queryset=StorageLocation.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.fields["destination_location"].queryset = StorageLocation.objects.filter(active=True, warehouse__active=True).select_related("warehouse")
        self.apply_arabic_widgets()


class CounterStartForm(ArabicFormMixin, forms.Form):
    """Worker enters the physical start value shown on the machine."""

    unit = forms.ModelChoiceField(label="وحدة العدّ", queryset=UnitOfMeasure.objects.none())
    start_reading = forms.DecimalField(
        label="قراءة العدّاد عند البداية", max_digits=20, decimal_places=4, min_value=0
    )
    started_at = forms.DateTimeField(label="وقت بدء القراءة", required=False)
    material_issue = forms.ModelChoiceField(
        label="صرف المادة المرتبط (اختياري)",
        queryset=None,
        required=False,
        help_text="للتتبع الجزئي للرول؛ اختر الصرف الذي وُزِّعت منه رولات هذه الفترة.",
    )
    roll_reference = forms.CharField(label="مرجع الرول", max_length=120, required=False)
    notes = forms.CharField(label="ملاحظات", max_length=240, required=False)
    continuation_of = forms.ModelChoiceField(
        label="متابعة لقراءة سابقة (عند إعادة ضبط العدّاد)",
        queryset=None,
        required=False,
    )
    reset_reason = forms.CharField(
        label="سبب إعادة الضبط / الاستمرار", max_length=240, required=False
    )

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = UnitOfMeasure.objects.filter(active=True)
        self.fields["material_issue"].queryset = order.material_issues.filter(
            movement__reversal__isnull=True
        )
        self.fields["continuation_of"].queryset = MachineCounterReading.objects.filter(
            order=order, end_reading__isnull=False
        )
        self.apply_arabic_widgets()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("continuation_of") and not (cleaned.get("reset_reason") or "").strip():
            self.add_error("reset_reason", "سبب إعادة الضبط إلزامي عند متابعة قراءة سابقة.")
        return cleaned


class CounterCloseForm(ArabicFormMixin, forms.Form):
    """Worker reads the end value; rejected quantity can never exceed output."""

    end_reading = forms.DecimalField(
        label="قراءة العدّاد عند النهاية", max_digits=20, decimal_places=4, min_value=0
    )
    ended_at = forms.DateTimeField(label="وقت إغلاق القراءة", required=False)
    rejected_quantity = forms.DecimalField(
        label="الكمية المرفوضة (إن لم يستبعدها العدّاد)",
        max_digits=20,
        decimal_places=4,
        min_value=0,
        required=False,
    )

    def __init__(self, *args, reading, **kwargs):
        super().__init__(*args, **kwargs)
        self.reading = reading
        self.fields["end_reading"].initial = reading.start_reading
        self.apply_arabic_widgets()


class CounterCorrectionForm(ArabicFormMixin, forms.Form):
    """Mandatory-reason correction; superseded values are archived by service."""

    reason = forms.CharField(
        label="سبب التصحيح (إلزامي)", max_length=240,
        help_text="يُحفظ في سجل التدقيق مع القيم المستبدلة.",
    )
    start_reading = forms.DecimalField(
        label="قراءة البداية الجديدة (اتركها فارغة لعدم تغييرها)",
        max_digits=20, decimal_places=4, min_value=0, required=False,
    )
    end_reading = forms.DecimalField(
        label="قراءة النهاية الجديدة (اتركها فارغة لعدم تغييرها)",
        max_digits=20, decimal_places=4, min_value=0, required=False,
    )
    rejected_quantity = forms.DecimalField(
        label="الكمية المرفوضة الجديدة (اتركها فارغة لعدم تغييرها)",
        max_digits=20, decimal_places=4, min_value=0, required=False,
    )
    notes = forms.CharField(label="ملاحظات جديدة", max_length=240, required=False)

    def __init__(self, *args, reading, **kwargs):
        super().__init__(*args, **kwargs)
        self.reading = reading
        self.apply_arabic_widgets()
