from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("", views.order_list, name="order_list"),
    path("orders/add/", views.order_create, name="order_add"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/edit/", views.order_edit, name="order_edit"),
    path("orders/<int:pk>/confirm/", views.order_confirm, name="order_confirm"),
    path("orders/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/complete/", views.order_complete, name="order_complete"),
    path("orders/<int:pk>/reopen/", views.order_reopen, name="order_reopen"),
    path("orders/<int:pk>/shipments/add/", views.shipment_create, name="shipment_add"),
    path("shipments/<uuid:pk>/", views.shipment_detail, name="shipment_detail"),
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/add/", views.customer_create, name="customer_add"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", views.customer_edit, name="customer_edit"),
]
