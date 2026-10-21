from decimal import Decimal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from catalog.models import Product, UnitOfMeasure
from costing.models import ProductSellingPrice
from inventory.services import to_base_quantity
from purchasing.models import PurchaseOrder
from sales.models import Customer, SalesOrder, SalesOrderLine, Shipment


def format_decimal(value, places=None):
    if value is None:
        return None
    value = Decimal(value)
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places))
    return format(value, "f")


class CustomerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = (
            "code", "name_ar", "name_en", "contact_person", "phone", "email",
            "address", "active",
        )
        read_only_fields = fields


class CustomerUnitSerializer(serializers.Serializer):
    code = serializers.CharField()
    name_ar = serializers.CharField()
    name_en = serializers.CharField(allow_blank=True)
    is_base = serializers.BooleanField(required=False)
    factor = serializers.CharField(required=False, allow_null=True)


class CustomerReferencePriceSerializer(serializers.Serializer):
    amount = serializers.CharField()
    unit = CustomerUnitSerializer()
    price_type = serializers.CharField()
    note = serializers.CharField()


class CustomerProductSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    base_uom = serializers.SerializerMethodField()
    units = serializers.SerializerMethodField()
    reference_price = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "sku", "name_ar", "name_en", "description", "category",
            "base_uom", "units", "reference_price",
        )
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_category(self, obj):
        return {
            "code": obj.category.code,
            "name_ar": obj.category.name_ar,
            "name_en": obj.category.name_en,
        }

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_base_uom(self, obj):
        return {
            "code": obj.base_uom.code,
            "name_ar": obj.base_uom.name_ar,
            "name_en": obj.base_uom.name_en,
        }

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_units(self, obj):
        units = [{
            "code": obj.base_uom.code,
            "name_ar": obj.base_uom.name_ar,
            "name_en": obj.base_uom.name_en,
            "is_base": True,
            "factor": None,
        }]
        for conversion in obj.unit_conversions.all():
            if not conversion.active or not conversion.unit.active:
                continue
            units.append({
                "code": conversion.unit.code,
                "name_ar": conversion.unit.name_ar,
                "name_en": conversion.unit.name_en,
                "is_base": False,
                "factor": format_decimal(conversion.factor),
            })
        return units

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_reference_price(self, obj):
        try:
            price = obj.selling_price_info
        except ProductSellingPrice.DoesNotExist:
            return None
        return {
            "amount": format_decimal(price.selling_price, 4),
            "unit": {
                "code": price.unit.code,
                "name_ar": price.unit.name_ar,
                "name_en": price.unit.name_en,
                "is_base": price.unit_id == obj.base_uom_id,
                "factor": None,
            },
            "price_type": "OWNER_REFERENCE_PRICE",
            "note": "سعر مرجعي للعرض فقط وليس عرض سعر أو سعرًا مؤكدًا للطلب.",
        }


class CustomerOrderLineSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()
    unit = CustomerUnitSerializer()
    quantity = serializers.SerializerMethodField()
    quantity_base = serializers.SerializerMethodField()
    unit_price = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()
    price_pending = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrderLine
        fields = (
            "product", "quantity", "unit", "quantity_base", "unit_price",
            "line_total", "notes", "price_pending",
        )
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_product(self, obj):
        return {
            "id": obj.product_id,
            "sku": obj.product.sku,
            "name_ar": obj.product.name_ar,
            "name_en": obj.product.name_en,
        }

    @extend_schema_field(OpenApiTypes.STR)
    def get_quantity(self, obj):
        return format_decimal(obj.quantity)

    @extend_schema_field(OpenApiTypes.STR)
    def get_quantity_base(self, obj):
        return format_decimal(obj.quantity_base)

    def _price_pending(self, obj):
        return bool(obj.order.customer_submitted and obj.unit_price <= 0)

    @extend_schema_field(OpenApiTypes.STR)
    def get_unit_price(self, obj):
        return None if self._price_pending(obj) else format_decimal(obj.unit_price, 4)

    @extend_schema_field(OpenApiTypes.STR)
    def get_line_total(self, obj):
        return None if self._price_pending(obj) else format_decimal(obj.line_total, 2)

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_price_pending(self, obj):
        return self._price_pending(obj)


class CustomerOrderListSerializer(serializers.ModelSerializer):
    ordered_value = serializers.SerializerMethodField()
    shipped_value = serializers.SerializerMethodField()
    shipment_state = serializers.CharField(read_only=True)
    shipment_state_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    pricing_status = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = (
            "order_number", "order_date", "requested_delivery_date",
            "customer_reference", "status", "status_display", "shipment_state",
            "shipment_state_display", "ordered_value", "shipped_value",
            "customer_submitted", "pricing_status", "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_ordered_value(self, obj):
        return format_decimal(obj.ordered_value, 2)

    @extend_schema_field(OpenApiTypes.STR)
    def get_shipped_value(self, obj):
        return format_decimal(obj.shipped_value, 2)

    @extend_schema_field(OpenApiTypes.STR)
    def get_shipment_state_display(self, obj):
        return obj.shipment_state_label

    @extend_schema_field(OpenApiTypes.STR)
    def get_pricing_status(self, obj):
        return obj.pricing_status


class CustomerOrderDetailSerializer(CustomerOrderListSerializer):
    lines = CustomerOrderLineSerializer(many=True, read_only=True)
    notes = serializers.CharField(read_only=True)

    class Meta(CustomerOrderListSerializer.Meta):
        fields = CustomerOrderListSerializer.Meta.fields + ("notes", "lines")


class CustomerOrderRequestLineSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(active=True, available_to_customers=True)
    )
    quantity = serializers.DecimalField(
        max_digits=30,
        decimal_places=12,
        min_value=Decimal("0.000000000001"),
    )
    unit = serializers.PrimaryKeyRelatedField(queryset=UnitOfMeasure.objects.filter(active=True))
    notes = serializers.CharField(max_length=240, required=False, allow_blank=True)

    def validate(self, attrs):
        try:
            to_base_quantity(attrs["product"], attrs["quantity"], attrs["unit"])
        except Exception as exc:
            raise serializers.ValidationError({"unit": exc.messages if hasattr(exc, "messages") else str(exc)})
        return attrs


class CustomerOrderRequestSerializer(serializers.Serializer):
    requested_delivery_date = serializers.DateField(required=False, allow_null=True)
    customer_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    lines = CustomerOrderRequestLineSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        unexpected = set(self.initial_data) - set(self.fields)
        if unexpected:
            raise serializers.ValidationError("حقول الطلب غير مسموح بها للعميل.")
        line_fields = set(self.fields["lines"].child.fields)
        for line in self.initial_data.get("lines", []):
            if set(line) - line_fields:
                raise serializers.ValidationError({"lines": "حقول البند غير مسموح بها للعميل."})
        return attrs

    def validate_lines(self, value):
        if len(value) > 50:
            raise serializers.ValidationError("لا يمكن إرسال أكثر من 50 بندًا في الطلب الواحد.")
        return value


class OwnerRecentSalesOrderSerializer(serializers.ModelSerializer):
    customer_code = serializers.CharField(source="customer.code", read_only=True)
    customer_name = serializers.CharField(source="customer.name_ar", read_only=True)
    ordered_value = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    shipment_state = serializers.CharField(read_only=True)

    class Meta:
        model = SalesOrder
        fields = (
            "order_number", "customer_code", "customer_name", "order_date",
            "status", "status_display", "ordered_value", "shipment_state",
            "customer_submitted",
        )
        read_only_fields = fields

    def get_ordered_value(self, obj):
        return format_decimal(obj.ordered_value, 2)


class OwnerRecentPurchaseOrderSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source="supplier.code", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name_ar", read_only=True)
    ordered_value = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    receipt_state = serializers.CharField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = (
            "order_number", "supplier_code", "supplier_name", "order_date",
            "status", "status_display", "ordered_value", "receipt_state",
        )
        read_only_fields = fields

    def get_ordered_value(self, obj):
        return format_decimal(obj.ordered_value, 2)


class OwnerRecentShipmentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    customer_code = serializers.CharField(source="order.customer.code", read_only=True)
    customer_name = serializers.CharField(source="order.customer.name_ar", read_only=True)
    net_value = serializers.SerializerMethodField()
    total_value = serializers.SerializerMethodField()
    reversed_line_count = serializers.IntegerField(read_only=True)
    is_fully_reversed = serializers.BooleanField(read_only=True)

    class Meta:
        model = Shipment
        fields = (
            "reference", "order_number", "customer_code", "customer_name",
            "shipment_date", "net_value", "total_value", "reversed_line_count",
            "is_fully_reversed",
        )
        read_only_fields = fields

    def get_net_value(self, obj):
        return format_decimal(obj.net_value, 2)

    def get_total_value(self, obj):
        return format_decimal(obj.total_value, 2)


class TokenResponseSerializer(serializers.Serializer):
    """Actual DRF token response: a single opaque token string."""

    token = serializers.CharField()


class DashboardQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    recent_limit = serializers.IntegerField(min_value=1, max_value=20, required=False, default=5)
    granularity = serializers.ChoiceField(choices=("day", "month"), required=False)

    def validate(self, attrs):
        start = attrs.get("start_date")
        end = attrs.get("end_date")
        if bool(start) != bool(end):
            raise serializers.ValidationError("يجب إرسال تاريخ البداية والنهاية معًا.")
        if start and end:
            if end < start:
                raise serializers.ValidationError("تاريخ النهاية يسبق تاريخ البداية.")
            if (end - start).days > 366:
                raise serializers.ValidationError("الحد الأقصى للفترة هو 366 يومًا.")
        return attrs


class DashboardResponseSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    period = serializers.DictField()
    sales = serializers.DictField()
    purchases = serializers.DictField()
    inventory = serializers.DictField()
    low_stock_alerts = serializers.ListField(child=serializers.DictField())
    production = serializers.DictField()
    recent = serializers.DictField()
    customers = serializers.DictField()
    suppliers = serializers.DictField()
    costing = serializers.DictField()
    operational_alerts = serializers.ListField(child=serializers.DictField())
    financial_integrity = serializers.DictField()
    data_sources = serializers.DictField()
