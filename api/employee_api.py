"""Thin operational API adapters over the manufacturing service layer."""
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from decimal import Decimal

from catalog.models import Product, UnitOfMeasure
from inventory.models import StorageLocation
from inventory.services import balance_for
from manufacturing import counter_services, services
from manufacturing.models import (
    MachineCounterReading, ProductionMaterialRequirement, ProductionOrder,
    ProductionOrderOperation, ProductionResult, ProductionMaterialWaste,
    MaterialIssue,
)
from .permissions import IsOperationalStaff


class InputSerializer(serializers.Serializer):
    """Reject undeclared fields so actor, role, cost and timestamps cannot be injected."""
    def to_internal_value(self, data):
        extra = set(data) - set(self.fields)
        if extra:
            raise ValidationError({key: "حقل غير مسموح." for key in sorted(extra)})
        return super().to_internal_value(data)


class ProgressInput(InputSerializer):
    order_id = serializers.IntegerField(min_value=1)
    operation_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=Decimal("0.000000000001"))
    unit_id = serializers.IntegerField(min_value=1)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=240)
    submission_key = serializers.UUIDField(required=False)


class ResultInput(InputSerializer):
    order_id = serializers.IntegerField(min_value=1)
    good_quantity = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0)
    scrap_quantity = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0)
    unit_id = serializers.IntegerField(min_value=1)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=240)
    submission_key = serializers.UUIDField(required=False)


class CompleteOperationInput(InputSerializer):
    order_id = serializers.IntegerField(min_value=1)
    operation_id = serializers.IntegerField(min_value=1)
    skip = serializers.BooleanField(required=False, default=False)


class IssueInput(InputSerializer):
    order_id = serializers.IntegerField(min_value=1)
    requirement_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=Decimal("0.000000000001"))
    unit_id = serializers.IntegerField(min_value=1)
    reference = serializers.CharField(max_length=100)


class WasteInput(InputSerializer):
    material_id = serializers.IntegerField(min_value=1)
    quantity_base = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=Decimal("0.000000000001"))
    material_issue_id = serializers.IntegerField(min_value=1, required=False)
    roll_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)


class ReverseInput(InputSerializer):
    reason = serializers.CharField(max_length=255)


class CounterStartInput(InputSerializer):
    order_id = serializers.IntegerField(min_value=1)
    unit_id = serializers.IntegerField(min_value=1)
    start_reading = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0)
    operation_id = serializers.IntegerField(min_value=1, required=False)
    material_issue_id = serializers.IntegerField(min_value=1, required=False)
    roll_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=240)
    continuation_id = serializers.IntegerField(min_value=1, required=False)
    reset_reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class CounterCloseInput(InputSerializer):
    end_reading = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0)
    rejected_quantity = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0, required=False)


class CounterCorrectInput(InputSerializer):
    reason = serializers.CharField(max_length=255)
    start_reading = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0, required=False)
    end_reading = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0, required=False)
    rejected_quantity = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=0, required=False)
    roll_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=240)


class ReceiptInput(InputSerializer):
    order_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=30, decimal_places=12, min_value=Decimal("0.000000000001"))
    unit_id = serializers.IntegerField(min_value=1)
    location_id = serializers.IntegerField(min_value=1)
    reference = serializers.CharField(max_length=100)


class EmployeeOperationOutput(serializers.Serializer):
    id = serializers.IntegerField()
    sequence = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()
    is_optional = serializers.BooleanField()


class EmployeeRequirementOutput(serializers.Serializer):
    requirement_id = serializers.IntegerField()
    material_id = serializers.IntegerField()
    material = serializers.CharField()
    unit_id = serializers.IntegerField()
    unit = serializers.CharField()
    planned_base = serializers.CharField()
    issued_base = serializers.CharField()
    remaining_base = serializers.CharField()
    available_at_source_base = serializers.CharField()
    shortage_base = serializers.CharField()


class EmployeeOrderOutput(serializers.Serializer):
    id = serializers.IntegerField()
    order_number = serializers.CharField()
    status = serializers.CharField()
    product_id = serializers.IntegerField()
    product = serializers.CharField()
    production_line = serializers.CharField()
    planned_quantity = serializers.CharField()
    planned_unit_id = serializers.IntegerField()
    unit = serializers.CharField()
    planned_quantity_base = serializers.CharField()
    source_location_id = serializers.IntegerField()
    operations = EmployeeOperationOutput(many=True)
    materials = EmployeeRequirementOutput(many=True)
    results = serializers.ListField(child=serializers.DictField())
    counters = serializers.ListField(child=serializers.DictField())
    material_waste = serializers.ListField(child=serializers.DictField())


class EmployeeMaterialOutput(serializers.Serializer):
    materials = serializers.ListField(child=serializers.DictField())
    locations = serializers.ListField(child=serializers.DictField())
    stock = serializers.ListField(child=serializers.DictField())


def _validated(request, serializer_class):
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _domain_error(exc):
    detail = getattr(exc, "message_dict", None) or exc.messages
    raise ValidationError(detail) from exc


class EmployeeOrderListView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(responses={200: EmployeeOrderOutput(many=True)}, operation_id="employee_production_orders_list")
    def get(self, request):
        orders = ProductionOrder.objects.filter(status__in=["RELEASED", "IN_PROGRESS"]).select_related(
            "product", "production_line", "planned_unit", "source_location"
        ).prefetch_related("operations", "requirements__material__base_uom", "results__unit",
                            "counter_readings__unit", "material_waste").order_by("created_at", "pk")
        return Response([_order_payload(o) for o in orders])


def _order_payload(order):
    requirements = []
    for req in order.requirements.select_related("material", "material__base_uom").prefetch_related("issues"):
        status_row = services.requirement_status(req)
        requirements.append({
            "requirement_id": req.pk, "material_id": req.material_id,
            "material": req.material.name_ar,
            "unit_id": req.material.base_uom_id,
            "unit": req.material.base_uom.code,
            "planned_base": str(status_row["planned"]),
            "issued_base": str(status_row["issued"]),
            "remaining_base": str(status_row["remaining"]),
            "available_at_source_base": str(status_row["available"]),
            "shortage_base": str(status_row["shortage"]),
        })
    return {
        "id": order.pk, "order_number": order.order_number, "status": order.status,
        "product_id": order.product_id, "product": str(order.product),
        "production_line": order.production_line.code,
        "planned_quantity": str(order.planned_quantity),
        "planned_unit_id": order.planned_unit_id, "unit": order.planned_unit.code,
        "planned_quantity_base": str(order.planned_quantity_base),
        "source_location_id": order.source_location_id,
        "operations": [{"id": op.pk, "sequence": op.sequence, "name": op.operation_name,
                        "status": op.status, "is_optional": op.is_optional}
                       for op in order.operations.all()],
        "materials": requirements,
        "results": [{"id": row.pk, "good_quantity": str(row.good_quantity),
                     "scrap_quantity": str(row.scrap_quantity), "unit": row.unit.code,
                     "posted_at": row.posted_at, "reversed": hasattr(row, "correction")}
                    for row in order.results.select_related("unit").all()],
        "counters": [{"id": row.pk, "status": row.status, "unit": row.unit.code,
                      "start_reading": str(row.start_reading),
                      "end_reading": str(row.end_reading) if row.end_reading is not None else None,
                      "counter_output": str(row.counter_output) if row.counter_output is not None else None,
                      "rejected_quantity": str(row.rejected_quantity) if row.rejected_quantity is not None else None,
                      "started_at": row.started_at, "ended_at": row.ended_at}
                     for row in order.counter_readings.select_related("unit").all()],
        "material_waste": [{"id": row.pk, "result_id": row.result_id,
                             "material_id": row.material_id, "quantity_base": str(row.quantity_base),
                             "posted_at": row.posted_at, "reversed": row.reversal_of_id is not None or hasattr(row, "reversal")}
                            for row in order.material_waste.select_related("material").all()],
    }


class EmployeeOrderDetailView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(responses={200: EmployeeOrderOutput}, operation_id="employee_production_order_retrieve")
    def get(self, request, pk):
        order = get_object_or_404(ProductionOrder.objects.filter(status__in=["RELEASED", "IN_PROGRESS"]), pk=pk)
        return Response(_order_payload(order))


class ProgressView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=ProgressInput, responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT}, operation_id="employee_production_progress_create")
    def post(self, request):
        d = _validated(request, ProgressInput)
        try:
            row, created = services.record_operation_progress(
                order=get_object_or_404(ProductionOrder, pk=d["order_id"]),
                order_operation=get_object_or_404(ProductionOrderOperation, pk=d["operation_id"]),
                quantity=d["quantity"], unit=get_object_or_404(UnitOfMeasure, pk=d["unit_id"]),
                notes=d.get("notes", ""), actor=request.user, submission_key=d.get("submission_key"),
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": row.pk, "created": created}, status=status.HTTP_201_CREATED if created else 200)


class CompleteOperationView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=CompleteOperationInput, responses={200: OpenApiTypes.OBJECT}, operation_id="employee_production_operation_complete")
    def post(self, request):
        d = _validated(request, CompleteOperationInput)
        try:
            row = services.complete_operation(
                order=get_object_or_404(ProductionOrder, pk=d["order_id"]),
                order_operation=get_object_or_404(ProductionOrderOperation, pk=d["operation_id"]),
                actor=request.user, skip=d["skip"],
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": row.pk, "status": row.status})


class ResultView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=ResultInput, responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT}, operation_id="employee_production_result_create")
    def post(self, request):
        d = _validated(request, ResultInput)
        try:
            row, created = services.record_production_result(
                order=get_object_or_404(ProductionOrder, pk=d["order_id"]),
                good_quantity=d["good_quantity"], scrap_quantity=d["scrap_quantity"],
                unit=get_object_or_404(UnitOfMeasure, pk=d["unit_id"]), notes=d.get("notes", ""),
                actor=request.user, submission_key=d.get("submission_key"),
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": row.pk, "posted_at": row.posted_at, "created": created}, status=201 if created else 200)


class ResultReverseView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=ReverseInput, responses={201: OpenApiTypes.OBJECT}, operation_id="employee_production_result_reverse")
    def post(self, request, pk):
        d = _validated(request, ReverseInput)
        try:
            reversal = services.reverse_production_result(
                result=get_object_or_404(ProductionResult, pk=pk), actor=request.user, reason=d["reason"]
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"result_id": reversal.result_id, "reversed_at": reversal.reversed_at}, status=201)


class WasteView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=WasteInput, responses={201: OpenApiTypes.OBJECT}, operation_id="employee_production_waste_create")
    def post(self, request, result_id):
        d = _validated(request, WasteInput)
        try:
            row = services.record_production_material_waste(
                result=get_object_or_404(ProductionResult, pk=result_id),
                material=get_object_or_404(Product, pk=d["material_id"]),
                quantity_base=d["quantity_base"], actor=request.user,
                material_issue=get_object_or_404(MaterialIssue, pk=d["material_issue_id"]) if d.get("material_issue_id") else None,
                roll_reference=d.get("roll_reference", ""),
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": row.pk, "posted_at": row.posted_at}, status=201)


class WasteReviewView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=None, responses={201: OpenApiTypes.OBJECT}, operation_id="employee_production_waste_review")
    def post(self, request, result_id):
        if request.data:
            raise ValidationError("لا يقبل هذا الإجراء أي حقول إضافية.")
        try:
            row = services.confirm_no_material_waste(result=get_object_or_404(ProductionResult, pk=result_id), actor=request.user)
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"result_id": row.result_id, "reviewed_at": row.reviewed_at}, status=201)


class WasteReverseView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=ReverseInput, responses={201: OpenApiTypes.OBJECT}, operation_id="employee_production_waste_reverse")
    def post(self, request, pk):
        d = _validated(request, ReverseInput)
        try:
            row = services.reverse_production_material_waste(
                waste=get_object_or_404(ProductionMaterialWaste, pk=pk),
                actor=request.user, reason=d["reason"],
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": row.pk, "reversed_record_id": row.reversal_of_id, "posted_at": row.posted_at}, status=201)


class MaterialDataView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(responses={200: EmployeeMaterialOutput}, operation_id="employee_materials_list")
    def get(self, request):
        materials = list(Product.objects.filter(
            active=True, category__code="raw-material"
        ).select_related("base_uom").order_by("sku"))
        locations = list(StorageLocation.objects.filter(
            active=True, warehouse__active=True
        ).select_related("warehouse").order_by("warehouse__code", "code"))
        return Response({
            "materials": [{"id": p.pk, "sku": p.sku, "name": p.name_ar,
                           "base_unit_id": p.base_uom_id, "base_unit": p.base_uom.code}
                          for p in materials],
            "locations": [{"id": l.pk, "warehouse": l.warehouse.code, "code": l.code, "name": l.name_ar}
                          for l in locations],
            "stock": [{"material_id": p.pk, "location_id": l.pk,
                       "quantity_base": str(balance_for(p, l)), "base_unit": p.base_uom.code}
                      for p in materials for l in locations],
        })


class MaterialIssueView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=IssueInput, responses={201: OpenApiTypes.OBJECT}, operation_id="employee_material_issue_create")
    def post(self, request):
        d = _validated(request, IssueInput)
        try:
            row = services.issue_material(
                order=get_object_or_404(ProductionOrder, pk=d["order_id"]),
                requirement=get_object_or_404(ProductionMaterialRequirement, pk=d["requirement_id"]),
                quantity=d["quantity"], unit=get_object_or_404(UnitOfMeasure, pk=d["unit_id"]),
                reference=d["reference"], actor=request.user,
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": str(row.pk), "posted_at": row.movement.posted_at}, status=201)


class CounterStartView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=CounterStartInput, responses={201: OpenApiTypes.OBJECT}, operation_id="employee_counter_start")
    def post(self, request):
        d = _validated(request, CounterStartInput)
        order = get_object_or_404(ProductionOrder, pk=d["order_id"])
        try:
            row = counter_services.start_counter_interval(
                actor=request.user, order=order, production_line=order.production_line,
                unit=get_object_or_404(UnitOfMeasure, pk=d["unit_id"]), start_reading=d["start_reading"],
                order_operation=get_object_or_404(ProductionOrderOperation, pk=d["operation_id"]) if d.get("operation_id") else None,
                material_issue=get_object_or_404(MaterialIssue, pk=d["material_issue_id"]) if d.get("material_issue_id") else None,
                roll_reference=d.get("roll_reference", ""), notes=d.get("notes", ""),
                continuation_of=get_object_or_404(MachineCounterReading, pk=d["continuation_id"]) if d.get("continuation_id") else None,
                reset_reason=d.get("reset_reason", ""),
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": row.pk, "status": row.status, "started_at": row.started_at}, status=201)


class CounterActionView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT}, operation_id="employee_counter_action")
    def post(self, request, pk, action):
        if action not in {"close", "approve", "correct"}:
            from django.http import Http404
            raise Http404
        row = get_object_or_404(MachineCounterReading, pk=pk)
        try:
            if action == "close":
                d = _validated(request, CounterCloseInput)
                row = counter_services.close_counter_interval(reading=row, actor=request.user, **d)
            elif action == "approve":
                if request.data:
                    raise ValidationError("لا يقبل الاعتماد أي حقول.")
                row = counter_services.approve_counter_reading(reading=row, actor=request.user)
            else:
                d = _validated(request, CounterCorrectInput)
                row = counter_services.correct_counter_reading(reading=row, actor=request.user, **d)
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": row.pk, "status": row.status})


class FinishedReceiptView(APIView):
    permission_classes = [IsOperationalStaff]

    @extend_schema(request=ReceiptInput, responses={201: OpenApiTypes.OBJECT}, operation_id="employee_finished_receipt_create")
    def post(self, request):
        d = _validated(request, ReceiptInput)
        try:
            row = services.receive_finished_goods(
                order=get_object_or_404(ProductionOrder, pk=d["order_id"]), quantity=d["quantity"],
                unit=get_object_or_404(UnitOfMeasure, pk=d["unit_id"]),
                destination_location=get_object_or_404(StorageLocation, pk=d["location_id"], active=True, warehouse__active=True),
                reference=d["reference"], actor=request.user,
            )
        except DjangoValidationError as exc:
            _domain_error(exc)
        return Response({"id": str(row.pk), "reference": row.reference, "posted_at": row.movement.posted_at}, status=201)
