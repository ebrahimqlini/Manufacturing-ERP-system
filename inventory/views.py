from decimal import Decimal

from django.contrib import messages
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponseForbidden
from django.utils.dateparse import parse_date

from catalog.models import Product
from catalog.models import ProductCategory
from .forms import StockMovementForm, StorageLocationForm, WarehouseForm
from .models import StockMovement, StorageLocation, Warehouse
from .services import ledger_balances, post_movement, reverse_movement


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("هذه الصفحة متاحة لمستخدمي النظام الداخليين فقط.")
        return view(request, *args, **kwargs)
    return wrapped


@login_required
def dashboard(request):
    return render(request, "inventory/dashboard.html", {
        "product_count": Product.objects.filter(active=True).count(),
        "warehouse_count": Warehouse.objects.filter(active=True).count(),
        "movement_count": StockMovement.objects.count(),
        "recent": StockMovement.objects.select_related("product", "unit", "actor").prefetch_related("ledger_entries__location__warehouse")[:10],
        "movement_types": [(key, label) for key, label in StockMovement.Type.choices if key not in (StockMovement.Type.REVERSAL, StockMovement.Type.OPENING)],
    })


@login_required
def warehouses(request):
    return render(request, "inventory/warehouses.html", {"warehouses": Warehouse.objects.prefetch_related("locations")})


@staff_required
def warehouse_form(request, pk=None):
    instance = get_object_or_404(Warehouse, pk=pk) if pk else None
    form = WarehouseForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ بيانات المخزن.")
        return redirect("inventory:warehouses")
    return render(request, "inventory/form.html", {"form": form, "title": "تعديل مخزن" if instance else "إضافة مخزن"})


@staff_required
def location_form(request, pk=None):
    instance = get_object_or_404(StorageLocation, pk=pk) if pk else None
    form = StorageLocationForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ بيانات الموقع.")
        return redirect("inventory:warehouses")
    return render(request, "inventory/form.html", {"form": form, "title": "تعديل موقع" if instance else "إضافة موقع"})


@login_required
def balances(request):
    products = Product.objects.select_related("category", "base_uom").order_by("sku")
    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "")
    if query:
        products = products.filter(Q(sku__icontains=query) | Q(name_ar__icontains=query) | Q(name_en__icontains=query))
    if category_id:
        products = products.filter(category_id=category_id)
    warehouse_id = request.GET.get("warehouse", "")
    locations = StorageLocation.objects.filter(active=True, warehouse__active=True).select_related("warehouse")
    if warehouse_id:
        locations = locations.filter(warehouse_id=warehouse_id)
    page = Paginator(products, 25).get_page(request.GET.get("page"))
    product_ids = {p.pk for p in page.object_list}
    location_rows = list(locations)
    location_ids = {loc.pk for loc in location_rows}
    totals = {(row["product_id"], row["location_id"]): row["balance"] for row in ledger_balances(product_ids=product_ids, location_ids=location_ids)}
    rows = [(product, location, totals.get((product.pk, location.pk), Decimal("0"))) for product in page.object_list for location in location_rows]
    return render(request, "inventory/balances.html", {"page": page, "rows": rows, "warehouses": Warehouse.objects.filter(active=True), "categories": ProductCategory.objects.filter(active=True), "warehouse_id": warehouse_id, "category_id": category_id, "q": query})


@login_required
def history(request):
    qs = StockMovement.objects.select_related("product", "unit", "actor", "source_location__warehouse", "destination_location__warehouse", "reversal_of")
    query = request.GET.get("q", "").strip()
    movement_type = request.GET.get("type", "")
    warehouse_id = request.GET.get("warehouse", "")
    location_id = request.GET.get("location", "")
    category_id = request.GET.get("category", "")
    since, until = parse_date(request.GET.get("from", "")), parse_date(request.GET.get("to", ""))
    if query:
        qs = qs.filter(Q(reference__icontains=query) | Q(external_reference__icontains=query) | Q(product__sku__icontains=query) | Q(product__name_ar__icontains=query))
    if movement_type:
        qs = qs.filter(movement_type=movement_type)
    if category_id:
        qs = qs.filter(product__category_id=category_id)
    if warehouse_id:
        qs = qs.filter(Q(source_location__warehouse_id=warehouse_id) | Q(destination_location__warehouse_id=warehouse_id))
    if location_id:
        qs = qs.filter(Q(source_location_id=location_id) | Q(destination_location_id=location_id))
    if since:
        qs = qs.filter(occurred_at__date__gte=since)
    if until:
        qs = qs.filter(occurred_at__date__lte=until)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "inventory/history.html", {"page": page, "types": StockMovement.Type.choices, "warehouses": Warehouse.objects.filter(active=True), "locations": StorageLocation.objects.filter(active=True).select_related("warehouse"), "categories": ProductCategory.objects.filter(active=True), "filters": request.GET})


@staff_required
def movement_form(request, movement_type):
    types = dict(StockMovement.Type.choices)
    if movement_type not in types or movement_type == StockMovement.Type.REVERSAL:
        return redirect("inventory:history")
    form = StockMovementForm(request.POST or None, movement_type=movement_type)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            post_movement(reference=data["reference"], movement_type=movement_type, product=data["product"], quantity=data["quantity"], unit=data["unit"], actor=request.user,
                          source_location=data.get("source_location"), destination_location=data.get("destination_location"), external_reference=data.get("external_reference", ""),
                          reason=data.get("reason", ""), notes=data.get("notes", ""))
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, errors in exc.message_dict.items():
                    for error in errors:
                        form.add_error(field if field in form.fields else None, error)
            else:
                form.add_error(None, exc.messages[0])
        else:
            messages.success(request, "تم ترحيل الحركة وتسجيل قيودها في دفتر المخزون.")
            return redirect("inventory:history")
    return render(request, "inventory/form.html", {"form": form, "title": types[movement_type], "submit_label": "ترحيل الحركة"})


@staff_required
def reverse_form(request, pk):
    movement = get_object_or_404(StockMovement, pk=pk)
    if request.method == "POST":
        try:
            reverse_movement(movement=movement, reference=request.POST.get("reference", "").strip(), actor=request.user, reason=request.POST.get("reason", "").strip())
        except ValidationError as exc:
            return render(request, "inventory/reverse.html", {"movement": movement, "error": " — ".join(exc.messages)}, status=400)
        messages.success(request, "تم تسجيل حركة العكس دون تعديل الحركة الأصلية.")
        return redirect("inventory:history")
    return render(request, "inventory/reverse.html", {"movement": movement})
