from django.urls import path

from . import views

app_name = "catalog"
urlpatterns = [
    path("", views.products, name="products"),
    path("add/", views.product_form, name="product_add"),
    path("<int:pk>/edit/", views.product_form, name="product_edit"),
    path("configuration/", views.configuration, name="configuration"),
    path("configuration/<str:kind>/add/", views.config_form, name="config_add"),
    path("configuration/<str:kind>/<int:pk>/edit/", views.config_form, name="config_edit"),
]
