from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("catalog/", include("catalog.urls")),
    path("inventory/", include("inventory.urls")),
    path("manufacturing/", include("manufacturing.urls")),
    path("costing/", include("costing.urls")),
    path("", include("core.urls")),
]
