from django.urls import path

from . import views

app_name = "inventory"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("warehouses/", views.warehouses, name="warehouses"),
    path("warehouses/add/", views.warehouse_form, name="warehouse_add"),
    path("warehouses/<int:pk>/edit/", views.warehouse_form, name="warehouse_edit"),
    path("locations/add/", views.location_form, name="location_add"),
    path("locations/<int:pk>/edit/", views.location_form, name="location_edit"),
    path("balances/", views.balances, name="balances"),
    path("history/", views.history, name="history"),
    path("movements/<str:movement_type>/", views.movement_form, name="movement"),
    path("movements/<uuid:pk>/reverse/", views.reverse_form, name="reverse"),
]
