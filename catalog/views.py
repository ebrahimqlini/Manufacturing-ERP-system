from django.contrib import messages
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProductCategoryForm, ProductForm, ProductUnitConversionForm, UnitOfMeasureForm
from .models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("هذه الصفحة متاحة لمستخدمي النظام الداخليين فقط.")
        return view(request, *args, **kwargs)
    return wrapped


@login_required
def products(request):
    qs = Product.objects.select_related("category", "base_uom")
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    if query:
        qs = qs.filter(sku__icontains=query) | qs.filter(name_ar__icontains=query) | qs.filter(name_en__icontains=query)
    if category:
        qs = qs.filter(category_id=category)
    page = Paginator(qs.order_by("sku"), 25).get_page(request.GET.get("page"))
    return render(request, "catalog/products.html", {"page": page, "categories": ProductCategory.objects.filter(active=True), "q": query, "category_id": category})


@staff_required
def product_form(request, pk=None):
    instance = get_object_or_404(Product, pk=pk) if pk else None
    form = ProductForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ بيانات الصنف.")
        return redirect("catalog:products")
    return render(request, "catalog/form.html", {"form": form, "title": "تعديل صنف" if instance else "إضافة صنف"})


@login_required
def configuration(request):
    return render(request, "catalog/configuration.html", {
        "categories": ProductCategory.objects.all(), "units": UnitOfMeasure.objects.all(),
        "conversions": ProductUnitConversion.objects.select_related("product", "unit"),
    })


@staff_required
def config_form(request, kind, pk=None):
    mappings = {"category": (ProductCategory, ProductCategoryForm), "unit": (UnitOfMeasure, UnitOfMeasureForm), "conversion": (ProductUnitConversion, ProductUnitConversionForm)}
    if kind not in mappings:
        return redirect("catalog:configuration")
    model, form_class = mappings[kind]
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ الإعداد.")
        return redirect("catalog:configuration")
    return render(request, "catalog/form.html", {"form": form, "title": "تعديل الإعداد" if instance else "إضافة إعداد"})
