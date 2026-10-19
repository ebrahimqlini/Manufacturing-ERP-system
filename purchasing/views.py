from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Product
from .forms import (
    PurchaseOrderForm,
    PurchaseOrderLineFormSet,
    PurchaseReceiptForm,
    SupplierForm,
)
from .models import PurchaseOrder, PurchaseReceipt, PurchaseReceiptLine, Supplier
from .services import cancel_order, confirm_order, create_receipt, save_draft_order


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


def _action_error(request, order, exc):
    messages.error(request, " — ".join(exc.messages))
    return redirect("purchasing:order_detail", pk=order.pk)


def _lines_from_formset(formset):
    lines = []
    for form in formset.forms:
        data = getattr(form, "cleaned_data", None)
        if not data or data.get("DELETE") or not data.get("material"):
            continue
        lines.append({
            "material": data["material"],
            "quantity": data["quantity"],
            "unit": data["unit"],
            "unit_price": data["unit_price"],
            "notes": data.get("notes") or "",
        })
    return lines


@staff_required
def order_list(request):
    orders = PurchaseOrder.objects.select_related("supplier", "created_by")
    status = request.GET.get("status", "")
    supplier = request.GET.get("supplier", "")
    query = request.GET.get("q", "").strip()
    if status in PurchaseOrder.Status.values:
        orders = orders.filter(status=status)
    if supplier.isdigit():
        orders = orders.filter(supplier_id=supplier)
    if query:
        orders = orders.filter(
            Q(order_number__icontains=query)
            | Q(supplier_reference__icontains=query)
            | Q(supplier__name_ar__icontains=query)
            | Q(supplier__code__icontains=query)
        )
    page = Paginator(orders, 25).get_page(request.GET.get("page"))
    return render(request, "purchasing/order_list.html", {
        "page": page,
        "statuses": PurchaseOrder.Status.choices,
        "suppliers": Supplier.objects.all(),
        "selected_status": status,
        "selected_supplier": supplier,
        "q": query,
    })


@staff_required
def order_create(request):
    order = PurchaseOrder()
    form = PurchaseOrderForm(request.POST or None, instance=order)
    formset = PurchaseOrderLineFormSet(request.POST or None, instance=order, prefix="lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        lines = _lines_from_formset(formset)
        try:
            order = save_draft_order(actor=request.user, lines=lines, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم حفظ أمر الشراء كمسودة. راجع البنود ثم أكّد الأمر.")
            return redirect("purchasing:order_detail", pk=order.pk)
    return render(request, "purchasing/order_form.html", {
        "form": form,
        "formset": formset,
        "title": "أمر شراء جديد",
    })


@staff_required
def order_edit(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if order.status != PurchaseOrder.Status.DRAFT:
        messages.error(request, "لا يمكن تعديل أمر شراء بعد تأكيده أو إلغائه.")
        return redirect("purchasing:order_detail", pk=order.pk)
    form = PurchaseOrderForm(request.POST or None, instance=order)
    formset = PurchaseOrderLineFormSet(request.POST or None, instance=order, prefix="lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        lines = _lines_from_formset(formset)
        try:
            order = save_draft_order(actor=request.user, lines=lines, order=order, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تحديث مسودة أمر الشراء.")
            return redirect("purchasing:order_detail", pk=order.pk)
    return render(request, "purchasing/order_form.html", {
        "form": form,
        "formset": formset,
        "order": order,
        "title": f"تعديل {order.order_number}",
    })


@staff_required
def order_detail(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "created_by", "confirmed_by", "cancelled_by"),
        pk=pk,
    )
    lines = list(order.lines.select_related("material", "material__base_uom", "unit"))
    receipts = order.receipts.select_related("destination_location", "actor").prefetch_related("lines")
    return render(request, "purchasing/order_detail.html", {
        "order": order,
        "lines": lines,
        "receipts": receipts,
        "has_receipts": order.receipts.exists(),
    })


@require_POST
@staff_required
def order_confirm(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    try:
        confirm_order(order=order, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم تأكيد أمر الشراء وقفل بنوده؛ يمكن الآن تسجيل الاستلامات.")
    return redirect("purchasing:order_detail", pk=order.pk)


@require_POST
@staff_required
def order_cancel(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    try:
        cancel_order(order=order, actor=request.user, reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم إلغاء أمر الشراء مع الاحتفاظ بسجل البنود والاستلامات.")
    return redirect("purchasing:order_detail", pk=order.pk)


@staff_required
def receipt_create(request, pk):
    order = get_object_or_404(PurchaseOrder.objects.select_related("supplier"), pk=pk)
    if order.status != PurchaseOrder.Status.CONFIRMED:
        messages.error(request, "لا يمكن الاستلام إلا لأمر شراء مؤكد.")
        return redirect("purchasing:order_detail", pk=order.pk)
    if order.receipt_state == PurchaseOrder.ReceiptState.RECEIVED:
        messages.info(request, "تم استلام كامل كميات هذا الأمر بالفعل.")
        return redirect("purchasing:order_detail", pk=order.pk)
    form = PurchaseReceiptForm(request.POST or None, order=order)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            receipt = create_receipt(
                order=order,
                actor=request.user,
                reference=data["reference"],
                received_date=data["received_date"],
                destination_location=data["destination_location"],
                supplier_reference=data.get("supplier_reference", ""),
                notes=data.get("notes", ""),
                lines=data["lines"],
            )
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تسجيل الاستلام وترحيل حركات المخزون المرتبطة به.")
            return redirect("purchasing:receipt_detail", pk=receipt.pk)
    return render(request, "purchasing/receipt_form.html", {
        "order": order,
        "form": form,
        "title": f"استلام مشتريات — {order.order_number}",
    })


@staff_required
def receipt_detail(request, pk):
    receipt = get_object_or_404(
        PurchaseReceipt.objects.select_related("order", "order__supplier", "destination_location", "actor"),
        pk=pk,
    )
    lines = receipt.lines.select_related("order_line__material", "unit", "movement")
    return render(request, "purchasing/receipt_detail.html", {"receipt": receipt, "lines": lines})


@staff_required
def supplier_list(request):
    suppliers = Supplier.objects.all()
    query = request.GET.get("q", "").strip()
    active = request.GET.get("active", "")
    if query:
        suppliers = suppliers.filter(
            Q(code__icontains=query)
            | Q(name_ar__icontains=query)
            | Q(name_en__icontains=query)
            | Q(contact_person__icontains=query)
            | Q(phone__icontains=query)
        )
    if active in ("1", "0"):
        suppliers = suppliers.filter(active=(active == "1"))
    page = Paginator(suppliers, 25).get_page(request.GET.get("page"))
    return render(request, "purchasing/supplier_list.html", {
        "page": page, "q": query, "selected_active": active,
    })


@staff_required
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        supplier = form.save(commit=False)
        supplier.created_by = request.user
        supplier.save()
        messages.success(request, "تمت إضافة المورد.")
        return redirect("purchasing:supplier_detail", pk=supplier.pk)
    return render(request, "purchasing/supplier_form.html", {"form": form, "title": "مورد جديد"})


@staff_required
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم تحديث بيانات المورد.")
        return redirect("purchasing:supplier_detail", pk=supplier.pk)
    return render(request, "purchasing/supplier_form.html", {
        "form": form, "supplier": supplier, "title": f"تعديل {supplier.name_ar}",
    })


@staff_required
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    orders = supplier.purchase_orders.select_related("created_by").order_by("-created_at")[:20]
    return render(request, "purchasing/supplier_detail.html", {"supplier": supplier, "orders": orders})


@staff_required
def price_history(request):
    """Read-only receipt-price traceability; it never changes costing prices."""
    material_id = request.GET.get("material", "")
    supplier_id = request.GET.get("supplier", "")
    lines = PurchaseReceiptLine.objects.select_related(
        "receipt", "receipt__order", "receipt__order__supplier", "order_line__material", "unit", "movement"
    )
    if material_id.isdigit():
        lines = lines.filter(order_line__material_id=material_id)
    if supplier_id.isdigit():
        lines = lines.filter(receipt__order__supplier_id=supplier_id)
    lines = lines.order_by("-receipt__received_date", "-receipt__created_at", "sequence")
    page = Paginator(lines, 50).get_page(request.GET.get("page"))
    return render(request, "purchasing/price_history.html", {
        "page": page,
        "materials": Product.objects.filter(purchase_order_lines__isnull=False).distinct().order_by("sku"),
        "suppliers": Supplier.objects.all(),
        "selected_material": material_id,
        "selected_supplier": supplier_id,
    })
