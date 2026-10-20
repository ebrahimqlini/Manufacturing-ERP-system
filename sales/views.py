from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    CustomerForm,
    SalesOrderForm,
    SalesOrderLineFormSet,
    ShipmentForm,
)
from .models import Customer, SalesOrder, Shipment
from .services import (
    cancel_order,
    complete_order,
    confirm_order,
    create_shipment,
    reopen_completed_order,
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


def _action_error(request, order, exc):
    messages.error(request, " — ".join(exc.messages))
    return redirect("sales:order_detail", pk=order.pk)


def _lines_from_formset(formset):
    lines = []
    for form in formset.forms:
        data = getattr(form, "cleaned_data", None)
        if not data or data.get("DELETE") or not data.get("product"):
            continue
        lines.append({
            "product": data["product"],
            "quantity": data["quantity"],
            "unit": data["unit"],
            "unit_price": data["unit_price"],
            "notes": data.get("notes") or "",
        })
    return lines


@staff_required
def order_list(request):
    orders = SalesOrder.objects.select_related("customer", "created_by")
    status = request.GET.get("status", "")
    customer = request.GET.get("customer", "")
    query = request.GET.get("q", "").strip()
    if status in SalesOrder.Status.values:
        orders = orders.filter(status=status)
    if customer.isdigit():
        orders = orders.filter(customer_id=customer)
    if query:
        orders = orders.filter(
            Q(order_number__icontains=query)
            | Q(customer_reference__icontains=query)
            | Q(customer__name_ar__icontains=query)
            | Q(customer__code__icontains=query)
        )
    page = Paginator(orders, 25).get_page(request.GET.get("page"))
    return render(request, "sales/order_list.html", {
        "page": page,
        "statuses": SalesOrder.Status.choices,
        "customers": Customer.objects.all(),
        "selected_status": status,
        "selected_customer": customer,
        "q": query,
    })


@staff_required
def order_create(request):
    order = SalesOrder()
    form = SalesOrderForm(request.POST or None, instance=order)
    formset = SalesOrderLineFormSet(request.POST or None, instance=order, prefix="lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        lines = _lines_from_formset(formset)
        try:
            order = save_draft_order(actor=request.user, lines=lines, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم حفظ أمر البيع كمسودة. راجع البنود ثم أكّد الأمر.")
            return redirect("sales:order_detail", pk=order.pk)
    return render(request, "sales/order_form.html", {
        "form": form,
        "formset": formset,
        "title": "أمر بيع جديد",
    })


@staff_required
def order_edit(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    if order.status != SalesOrder.Status.DRAFT:
        messages.error(request, "لا يمكن تعديل أمر بيع بعد تأكيده أو إلغائه.")
        return redirect("sales:order_detail", pk=order.pk)
    form = SalesOrderForm(request.POST or None, instance=order)
    formset = SalesOrderLineFormSet(request.POST or None, instance=order, prefix="lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        lines = _lines_from_formset(formset)
        try:
            order = save_draft_order(actor=request.user, lines=lines, order=order, **form.cleaned_data)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تحديث مسودة أمر البيع.")
            return redirect("sales:order_detail", pk=order.pk)
    return render(request, "sales/order_form.html", {
        "form": form,
        "formset": formset,
        "order": order,
        "title": f"تعديل {order.order_number}",
    })


@staff_required
def order_detail(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related("customer", "created_by", "confirmed_by", "completed_by", "cancelled_by"),
        pk=pk,
    )
    lines = list(order.lines.select_related("product", "product__base_uom", "unit"))
    shipments = order.shipments.select_related("source_location", "actor").prefetch_related("lines")
    return render(request, "sales/order_detail.html", {
        "order": order,
        "lines": lines,
        "shipments": shipments,
        "has_shipments": order.shipments.exists(),
    })


@require_POST
@staff_required
def order_confirm(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    try:
        confirm_order(order=order, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم تأكيد أمر البيع وقفل بنوده؛ يمكن الآن تسجيل الشحنات.")
    return redirect("sales:order_detail", pk=order.pk)


@require_POST
@staff_required
def order_cancel(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    try:
        cancel_order(order=order, actor=request.user, reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم إلغاء أمر البيع مع الاحتفاظ بسجل البنود والشحنات.")
    return redirect("sales:order_detail", pk=order.pk)


@require_POST
@staff_required
def order_complete(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    try:
        complete_order(order=order, actor=request.user)
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تم إكمال أمر البيع.")
    return redirect("sales:order_detail", pk=order.pk)


@require_POST
@staff_required
def order_reopen(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    try:
        reopen_completed_order(order=order, actor=request.user, reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "تمت إعادة فتح أمر البيع؛ يمكن شحن الكميات المعلقة ثم إكماله من جديد.")
    return redirect("sales:order_detail", pk=order.pk)


@staff_required
def shipment_create(request, pk):
    order = get_object_or_404(SalesOrder.objects.select_related("customer"), pk=pk)
    if order.status != SalesOrder.Status.CONFIRMED:
        messages.error(request, "لا يمكن الشحن إلا لأمر بيع مؤكد.")
        return redirect("sales:order_detail", pk=order.pk)
    if order.shipment_state == SalesOrder.ShipmentState.SHIPPED:
        messages.info(request, "تم شحن كامل كميات هذا الأمر بالفعل.")
        return redirect("sales:order_detail", pk=order.pk)
    form = ShipmentForm(request.POST or None, order=order)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            shipment = create_shipment(
                order=order,
                actor=request.user,
                reference=data["reference"],
                shipment_date=data["shipment_date"],
                source_location=data["source_location"],
                customer_reference=data.get("customer_reference", ""),
                notes=data.get("notes", ""),
                lines=data["lines"],
            )
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "تم تسجيل الشحن وترحيل حركات المخزون المرتبطة به.")
            return redirect("sales:shipment_detail", pk=shipment.pk)
    return render(request, "sales/shipment_form.html", {
        "order": order,
        "form": form,
        "title": f"شحن مبيعات — {order.order_number}",
    })


@staff_required
def shipment_detail(request, pk):
    shipment = get_object_or_404(
        Shipment.objects.select_related("order", "order__customer", "source_location", "actor"),
        pk=pk,
    )
    lines = shipment.lines.select_related("order_line__product", "unit", "movement")
    return render(request, "sales/shipment_detail.html", {"shipment": shipment, "lines": lines})


@staff_required
def customer_list(request):
    customers = Customer.objects.all()
    query = request.GET.get("q", "").strip()
    active = request.GET.get("active", "")
    if query:
        customers = customers.filter(
            Q(code__icontains=query)
            | Q(name_ar__icontains=query)
            | Q(name_en__icontains=query)
            | Q(contact_person__icontains=query)
            | Q(phone__icontains=query)
        )
    if active in ("1", "0"):
        customers = customers.filter(active=(active == "1"))
    page = Paginator(customers, 25).get_page(request.GET.get("page"))
    return render(request, "sales/customer_list.html", {
        "page": page, "q": query, "selected_active": active,
    })


@staff_required
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save(commit=False)
        customer.created_by = request.user
        customer.save()
        messages.success(request, "تمت إضافة العميل.")
        return redirect("sales:customer_detail", pk=customer.pk)
    return render(request, "sales/customer_form.html", {"form": form, "title": "عميل جديد"})


@staff_required
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم تحديث بيانات العميل.")
        return redirect("sales:customer_detail", pk=customer.pk)
    return render(request, "sales/customer_form.html", {
        "form": form, "customer": customer, "title": f"تعديل {customer.name_ar}",
    })


@staff_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    orders = customer.sales_orders.select_related("created_by").order_by("-created_at")[:20]
    return render(request, "sales/customer_detail.html", {"customer": customer, "orders": orders})
