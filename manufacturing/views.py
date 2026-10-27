from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    BOMComponentFormSet,
    BOMForm,
    CounterCloseForm,
    CounterCorrectionForm,
    CounterStartForm,
    FinishedGoodsReceiptForm,
    MaterialIssueForm,
    OperationProgressForm,
    ProductionLineForm,
    ProductionOperationForm,
    ProductionOrderForm,
    ProductionResultForm,
    ProductionMaterialWasteForm,
    ProductionResultCorrectionForm,
    ProductionWasteReversalForm,
    RoutingForm,
    RoutingStepFormSet,
)
from .counter_services import (
    approve_counter_reading,
    close_counter_interval,
    correct_counter_reading,
    start_counter_interval,
)
from .models import (
    BillOfMaterials,
    MachineCounterReading,
    ProductionLine,
    ProductionOperation,
    ProductionOrder,
    ProductionResult,
    ProductionOrderOperation,
    Routing,
)
from .services import (
    cancel_order,
    complete_operation,
    complete_order,
    issue_material,
    order_summary,
    receive_finished_goods,
    receive_replacement_finished_goods,
    record_operation_progress,
    record_production_result,
    record_production_material_waste,
    confirm_no_material_waste,
    reverse_production_result,
    reverse_production_material_waste,
    release_order,
    requirement_status,
    save_draft_order,
)


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("هذه الصفحة متاحة لمستخدمي النظام الداخليين فقط.")
        return view(request, *args, **kwargs)
    return wrapped


def _add_validation_error(form, exc):
    for message in exc.messages:
        form.add_error(None, message)


@login_required
def order_list(request):
    orders = ProductionOrder.objects.select_related("product", "production_line", "created_by")
    status = request.GET.get("status", "")
    line = request.GET.get("line", "")
    if status in ProductionOrder.Status.values:
        orders = orders.filter(status=status)
    if line.isdigit():
        orders = orders.filter(production_line_id=line)
    return render(request, "manufacturing/order_list.html", {
        "orders": orders[:100], "statuses": ProductionOrder.Status.choices,
        "lines": ProductionLine.objects.filter(active=True), "selected_status": status, "selected_line": line,
    })


@staff_required
def order_create(request):
    form = ProductionOrderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            order = save_draft_order(actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم حفظ أمر الإنتاج كمسودة. اضبط قائمة المواد والمسار قبل الإطلاق.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": "أمر إنتاج جديد"})


@staff_required
def order_edit(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    if order.status != ProductionOrder.Status.DRAFT:
        messages.error(request, "لا يمكن تعديل أمر إنتاج بعد إطلاقه.")
        return redirect("manufacturing:order_detail", pk=order.pk)
    form = ProductionOrderForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        try:
            order = save_draft_order(actor=request.user, order=order, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تحديث المسودة.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"تعديل {order.order_number}"})


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        ProductionOrder.objects.select_related("product", "product__base_uom", "production_line", "bom", "routing", "source_location"),
        pk=pk,
    )
    requirements = [requirement_status(row) for row in order.requirements.select_related("material", "material__base_uom")]
    return render(request, "manufacturing/order_detail.html", {
        "order": order, "requirements": requirements, "summary": order_summary(order),
        "operations": order.operations.select_related("operation").prefetch_related("progress_entries"),
        "results": order.results.select_related("unit", "actor", "waste_review").prefetch_related("material_waste__material", "material_waste__actor"),
        "receipts": order.finished_receipts.select_related("movement", "destination_location", "actor"),
        "issues": order.material_issues.select_related("requirement__material", "unit", "actor", "movement"),
        "counter_readings": order.counter_readings.select_related(
            "unit", "entered_by", "closed_by", "approved_by", "corrected_by",
            "production_line", "material_issue",
        ),
        "statuses": dict(ProductionOrder.Status.choices),
    })


def _action_error(request, order, exc):
    messages.error(request, " — ".join(exc.messages))
    return redirect("manufacturing:order_detail", pk=order.pk)


@require_POST
@staff_required
def order_release(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    try:
        release_order(order=order, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم إطلاق الأمر وحفظ نسخة ثابتة من قائمة المواد ومسار الإنتاج.")
    return redirect("manufacturing:order_detail", pk=order.pk)


@require_POST
@staff_required
def order_complete(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    try:
        complete_order(order=order, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم إكمال أمر الإنتاج.")
    return redirect("manufacturing:order_detail", pk=order.pk)


@require_POST
@staff_required
def order_cancel(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    try:
        cancel_order(order=order, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "أُلغي الأمر مع الاحتفاظ بسجل الحركات والإنتاج السابقة.")
    return redirect("manufacturing:order_detail", pk=order.pk)


@staff_required
def material_issue_form(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    form = MaterialIssueForm(request.POST or None, order=order)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            issue_material(order=order, actor=request.user, **data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم صرف الكمية الفعلية وربطها بدفتر المخزون وأمر الإنتاج.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"صرف مواد — {order.order_number}", "help_text": "الصرف الجزئي مسموح ضمن المتبقي، ويظل النقص معلومة لا حركة مخزون."})


@staff_required
def operation_progress_form(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    form = OperationProgressForm(request.POST or None, order=order)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            _, created = record_operation_progress(order=order, actor=request.user, **data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.info(request, "تم تسجيل هذا الإرسال من قبل؛ لم يُضف تقدم مكرر." if not created else "تم حفظ تقدم العملية.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"تسجيل تقدم — {order.order_number}"})


@require_POST
@staff_required
def operation_finish(request, pk, operation_pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    step = get_object_or_404(ProductionOrderOperation, pk=operation_pk, order=order)
    try:
        complete_operation(order=order, order_operation=step, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم إكمال العملية.")
    return redirect("manufacturing:order_detail", pk=order.pk)


@require_POST
@staff_required
def operation_skip(request, pk, operation_pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    step = get_object_or_404(ProductionOrderOperation, pk=operation_pk, order=order)
    try:
        complete_operation(order=order, order_operation=step, actor=request.user, skip=True)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم تجاوز العملية الاختيارية وتسجيل ذلك في الأمر.")
    return redirect("manufacturing:order_detail", pk=order.pk)


@staff_required
def production_result_form(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    form = ProductionResultForm(request.POST or None, order=order)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            _, created = record_production_result(order=order, actor=request.user, **data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.info(request, "تم تسجيل هذا الإرسال من قبل؛ لم يُضف إنتاج مكرر." if not created else "تم تسجيل الإنتاج الجيد والهالك كبيانات إنتاج؛ استلام المنتج النهائي خطوة منفصلة.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"تسجيل إنتاج — {order.order_number}", "help_text": "العائد = الإنتاج الجيد ÷ (الجيد + الهالك) × 100. لا يُحتسب عند عدم وجود كمية إنتاج."})


@staff_required
def production_material_waste_form(request, pk, result_pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    result = get_object_or_404(ProductionResult, pk=result_pk, order=order)
    form = ProductionMaterialWasteForm(request.POST or None, order=order)
    if request.method == "POST" and form.is_valid():
        try:
            record_production_material_waste(result=result, actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تسجيل المادة التالفة ولقطة تكلفتها وقت الترحيل.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"تسجيل هالك مواد — {order.order_number}"})


@require_POST
@staff_required
def confirm_production_waste_review(request, pk, result_pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    result = get_object_or_404(ProductionResult, pk=result_pk, order=order)
    try:
        confirm_no_material_waste(result=result, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم تأكيد مراجعة هالك المواد لهذه النتيجة، بما في ذلك عدم وجود هالك.")
    return redirect("manufacturing:order_detail", pk=order.pk)


@staff_required
def production_result_correction_form(request, pk, result_pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    result = get_object_or_404(ProductionResult, pk=result_pk, order=order)
    form = ProductionResultCorrectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reverse_production_result(result=result, actor=request.user, reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم عكس النتيجة بسجل تدقيق. أعد تسجيل الكميات الصحيحة كإنتاج جديد عند الحاجة.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"عكس نتيجة إنتاج — {order.order_number}"})


@staff_required
def production_waste_reversal_form(request, pk, result_pk, waste_pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    result = get_object_or_404(ProductionResult, pk=result_pk, order=order)
    waste = get_object_or_404(result.material_waste, pk=waste_pk, reversal_of__isnull=True)
    form = ProductionWasteReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reverse_production_material_waste(waste=waste, actor=request.user, reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم عكس سجل هالك المواد بسجل تعويضي مع الحفاظ على القيمة الأصلية.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"عكس هالك مواد — {order.order_number}"})


@staff_required
def finished_receipt_form(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    form = FinishedGoodsReceiptForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            receive_finished_goods(order=order, actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم استلام المنتج النهائي في المخزون المشترك.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {"form": form, "title": f"استلام منتج نهائي — {order.order_number}"})


@staff_required
def finished_receipt_replacement_form(request, pk, receipt_pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    original = get_object_or_404(order.finished_receipts.select_related("movement"), pk=receipt_pk)
    form = FinishedGoodsReceiptForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            receive_replacement_finished_goods(order=order, original_receipt=original,
                                               actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تسجيل استلام تعويضي مرتبط بالاستلام المعكوس؛ حالة الأمر لم تتغير.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {
        "form": form, "title": f"استلام تعويضي — {order.order_number}",
        "help_text": f"تعويض الاستلام المعكوس {original.reference} دون حذف سجل الحركة الأصلية.",
    })


@login_required
def configuration(request):
    return render(request, "manufacturing/configuration.html", {
        "lines": ProductionLine.objects.all(), "operations": ProductionOperation.objects.all(),
        "boms": BillOfMaterials.objects.select_related("product").prefetch_related("components"),
        "routings": Routing.objects.select_related("product", "production_line").prefetch_related("steps__operation"),
    })


@staff_required
def line_form(request, pk=None):
    instance = get_object_or_404(ProductionLine, pk=pk) if pk else None
    form = ProductionLineForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ خط الإنتاج.")
        return redirect("manufacturing:configuration")
    return render(request, "manufacturing/form.html", {"form": form, "title": "خط إنتاج"})


@staff_required
def operation_form(request, pk=None):
    instance = get_object_or_404(ProductionOperation, pk=pk) if pk else None
    form = ProductionOperationForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ العملية.")
        return redirect("manufacturing:configuration")
    return render(request, "manufacturing/form.html", {"form": form, "title": "عملية إنتاج"})


@staff_required
def bom_form(request, pk=None):
    instance = get_object_or_404(BillOfMaterials, pk=pk) if pk else None
    locked = bool(instance and instance.orders.exclude(status=ProductionOrder.Status.DRAFT).exists())
    form = BOMForm(request.POST or None, instance=instance)
    if instance is None:
        form.instance.created_by = request.user
    formset_class = BOMComponentFormSet
    next_sequence = (instance.components.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 10 if instance else 10
    formset = formset_class(request.POST or None, instance=form.instance, initial=[{"sequence": next_sequence + 10 * i} for i in range(3)])
    if request.method == "POST":
        if locked:
            form.add_error(None, "هذا الإصدار مستخدم في أمر مطلق؛ أنشئ إصدارًا جديدًا بدل تعديله.")
        if form.is_valid() and formset.is_valid() and not locked:
            try:
                with transaction.atomic():
                    bom = form.save()
                    formset.instance = bom
                    formset.save()
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                messages.success(request, "تم حفظ قائمة المواد ومكوناتها. أدخل فقط بيانات المصنع المعتمدة.")
                return redirect("manufacturing:configuration")
    return render(request, "manufacturing/formset.html", {
        "form": form, "formset": formset, "title": "قائمة مواد (BOM)",
        "formset_title": "المكونات المطلوبة لكمية الأساس المحددة في القائمة", "locked": locked,
    })


@staff_required
def routing_form(request, pk=None):
    instance = get_object_or_404(Routing, pk=pk) if pk else None
    locked = bool(instance and instance.orders.exclude(status=ProductionOrder.Status.DRAFT).exists())
    form = RoutingForm(request.POST or None, instance=instance)
    if instance is None:
        form.instance.created_by = request.user
    next_sequence = (instance.steps.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1 if instance else 1
    formset = RoutingStepFormSet(request.POST or None, instance=form.instance, initial=[{"sequence": next_sequence + i} for i in range(4)])
    if request.method == "POST":
        if locked:
            form.add_error(None, "هذا الإصدار مستخدم في أمر مطلق؛ أنشئ إصدارًا جديدًا بدل تعديله.")
        if form.is_valid() and formset.is_valid() and not locked:
            try:
                with transaction.atomic():
                    routing = form.save()
                    formset.instance = routing
                    formset.save()
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                messages.success(request, "تم حفظ المسار. أضف العمليات التي تنطبق فعليًا على المنتج فقط.")
                return redirect("manufacturing:configuration")
    return render(request, "manufacturing/formset.html", {
        "form": form, "formset": formset, "title": "مسار إنتاج",
        "formset_title": "رتّب العمليات المطلوبة فقط؛ يمكن تجاوز عملية غير مطلوبة بتكوين مسار مناسب.", "locked": locked,
    })


# --- Manual machine counter readings (worker reads the physical counter) ---


@staff_required
def counter_start_form(request, pk):
    order = get_object_or_404(
        ProductionOrder.objects.select_related("product", "production_line"), pk=pk
    )
    form = CounterStartForm(request.POST or None, order=order)
    if request.method == "POST" and form.is_valid():
        try:
            reading = start_counter_interval(
                actor=request.user,
                order=order,
                production_line=order.production_line,
                unit=form.cleaned_data["unit"],
                start_reading=form.cleaned_data["start_reading"],
                started_at=form.cleaned_data.get("started_at"),
                material_issue=form.cleaned_data.get("material_issue"),
                roll_reference=form.cleaned_data.get("roll_reference", ""),
                notes=form.cleaned_data.get("notes", ""),
                continuation_of=form.cleaned_data.get("continuation_of"),
                reset_reason=form.cleaned_data.get("reset_reason", ""),
            )
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم فتح فترة قراءة العدّاد؛ سجّل قراءة النهاية عند الإغلاق.")
            return redirect("manufacturing:order_detail", pk=order.pk)
    return render(request, "manufacturing/form.html", {
        "form": form, "title": f"بدء قراءة عدّاد — {order.order_number}",
    })


@staff_required
def counter_close_form(request, pk):
    reading = get_object_or_404(MachineCounterReading.objects.select_related("order"), pk=pk)
    form = CounterCloseForm(request.POST or None, reading=reading)
    if request.method == "POST" and form.is_valid():
        try:
            close_counter_interval(
                reading=reading,
                actor=request.user,
                end_reading=form.cleaned_data["end_reading"],
                ended_at=form.cleaned_data.get("ended_at"),
                rejected_quantity=form.cleaned_data.get("rejected_quantity"),
            )
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم إغلاق القراءة؛ لا تُعتمد أرقامها في التقارير إلا بعد الاعتماد.")
            return redirect("manufacturing:order_detail", pk=reading.order_id)
    return render(request, "manufacturing/form.html", {
        "form": form, "title": f"إغلاق قراءة العدّاد — {reading.order.order_number}",
    })


@require_POST
@staff_required
def counter_approve(request, pk):
    reading = get_object_or_404(MachineCounterReading.objects.select_related("order"), pk=pk)
    try:
        approve_counter_reading(reading=reading, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, reading.order, exc)
    messages.success(request, "تم اعتماد قراءة العدّاد.")
    return redirect("manufacturing:order_detail", pk=reading.order_id)


@staff_required
def counter_correct_form(request, pk):
    reading = get_object_or_404(MachineCounterReading.objects.select_related("order"), pk=pk)
    form = CounterCorrectionForm(request.POST or None, reading=reading)
    if request.method == "POST" and form.is_valid():
        try:
            correct_counter_reading(
                reading=reading,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                start_reading=form.cleaned_data.get("start_reading"),
                end_reading=form.cleaned_data.get("end_reading"),
                rejected_quantity=form.cleaned_data.get("rejected_quantity"),
                notes=form.cleaned_data.get("notes") or None,
            )
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تسجيل التصحيح في سجل التدقيق؛ أعد اعتماد القراءة إن لزم.")
            return redirect("manufacturing:order_detail", pk=reading.order_id)
    return render(request, "manufacturing/form.html", {
        "form": form, "title": f"تصحيح قراءة العدّاد — {reading.order.order_number}",
    })
