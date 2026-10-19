from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.order_list, name="order_list"),
    path("orders/add/", views.order_create, name="order_add"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/edit/", views.order_edit, name="order_edit"),
    path("orders/<int:pk>/confirm/", views.order_confirm, name="order_confirm"),
    path("orders/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/receipts/add/", views.receipt_create, name="receipt_add"),
    path("receipts/<uuid:pk>/", views.receipt_detail, name="receipt_detail"),
    path("suppliers/", views.supplier_list, name="supplier_list"),
    path("suppliers/add/", views.supplier_create, name="supplier_add"),
    path("suppliers/<int:pk>/", views.supplier_detail, name="supplier_detail"),
    path("suppliers/<int:pk>/edit/", views.supplier_edit, name="supplier_edit"),
    path("prices/", views.price_history, name="price_history"),
]
