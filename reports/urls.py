from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("sales/", views.sales, name="sales"),
    path("purchases/", views.purchases, name="purchases"),
    path("inventory/", views.inventory, name="inventory"),
    path("production/", views.production, name="production"),
    path("costing/", views.costing, name="costing"),
    path("profit/", views.profit, name="profit"),
]
