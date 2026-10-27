from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAdminUser

from .views import (
    CustomerOrderDetailView,
    CustomerOrderListView,
    CustomerOrderRequestView,
    CustomerPriceListView,
    CustomerProductDetailView,
    CustomerProductListView,
    CustomerProfileView,
    OwnerCostingPeriodListView,
    OwnerCostingView,
    OwnerDashboardView,
    OwnerInventoryView,
    OwnerProductionOrderListView,
    OwnerProductionView,
    OwnerProfitView,
    OwnerPurchaseOrderListView,
    OwnerPurchasesView,
    OwnerSalesOrderListView,
    OwnerSalesView,
    OwnerStockListView,
    OwnerStockMovementListView,
    TokenLogoutView,
    TokenObtainView,
    CurrentUserView,
)
from .employee_api import (
    CompleteOperationView, CounterActionView, CounterStartView, EmployeeOrderDetailView, EmployeeOrderListView,
    FinishedReceiptView, MaterialDataView, MaterialIssueView, ProgressView, ResultReverseView,
    ResultView, WasteReviewView, WasteReverseView, WasteView,
)

app_name = "api"

# Documentation is for logged-in staff only. It accepts a staff Django session
# so the schema and Swagger UI work in a browser, or a token for CLI clients.
# The actual API endpoints remain token-only through settings.
DOCS_AUTHENTICATION_CLASSES = [SessionAuthentication, TokenAuthentication]

urlpatterns = [
    path("auth/token/", TokenObtainView.as_view(), name="token"),
    path("auth/logout/", TokenLogoutView.as_view(), name="logout"),
    path("auth/me/", CurrentUserView.as_view(), name="me"),
    path("employee/production/orders/", EmployeeOrderListView.as_view(), name="employee-production-orders"),
    path("employee/production/orders/<int:pk>/", EmployeeOrderDetailView.as_view(), name="employee-production-order-detail"),
    path("employee/production/progress/", ProgressView.as_view(), name="employee-production-progress"),
    path("employee/production/operations/complete/", CompleteOperationView.as_view(), name="employee-production-operation-complete"),
    path("employee/production/results/", ResultView.as_view(), name="employee-production-results"),
    path("employee/production/results/<int:pk>/reverse/", ResultReverseView.as_view(), name="employee-production-result-reverse"),
    path("employee/production/results/<int:result_id>/waste/", WasteView.as_view(), name="employee-production-waste"),
    path("employee/production/results/<int:result_id>/waste-review/", WasteReviewView.as_view(), name="employee-production-waste-review"),
    path("employee/production/waste/<int:pk>/reverse/", WasteReverseView.as_view(), name="employee-production-waste-reverse"),
    path("employee/materials/", MaterialDataView.as_view(), name="employee-materials"),
    path("employee/material-issues/", MaterialIssueView.as_view(), name="employee-material-issues"),
    path("employee/counters/", CounterStartView.as_view(), name="employee-counter-start"),
    path("employee/counters/<int:pk>/<str:action>/", CounterActionView.as_view(), name="employee-counter-action"),
    path("employee/finished-receipts/", FinishedReceiptView.as_view(), name="employee-finished-receipts"),
    path("customer/profile/", CustomerProfileView.as_view(), name="customer-profile"),
    path("customer/products/", CustomerProductListView.as_view(), name="customer-products"),
    path("customer/products/<int:pk>/", CustomerProductDetailView.as_view(), name="customer-product-detail"),
    path("customer/prices/", CustomerPriceListView.as_view(), name="customer-prices"),
    path("customer/orders/", CustomerOrderListView.as_view(), name="customer-orders"),
    path("customer/orders/<str:order_number>/", CustomerOrderDetailView.as_view(), name="customer-order-detail"),
    path("customer/order-requests/", CustomerOrderRequestView.as_view(), name="customer-order-requests"),
    path("owner/dashboard/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("owner/sales/", OwnerSalesView.as_view(), name="owner-sales"),
    path("owner/purchases/", OwnerPurchasesView.as_view(), name="owner-purchases"),
    path("owner/inventory/", OwnerInventoryView.as_view(), name="owner-inventory"),
    path("owner/production/", OwnerProductionView.as_view(), name="owner-production"),
    path("owner/costing/", OwnerCostingView.as_view(), name="owner-costing"),
    path("owner/profit/", OwnerProfitView.as_view(), name="owner-profit"),
    path(
        "owner/sales/orders/",
        OwnerSalesOrderListView.as_view(),
        name="owner-sales-orders",
    ),
    path(
        "owner/purchases/orders/",
        OwnerPurchaseOrderListView.as_view(),
        name="owner-purchase-orders",
    ),
    path(
        "owner/inventory/stock/",
        OwnerStockListView.as_view(),
        name="owner-stock",
    ),
    path(
        "owner/inventory/movements/",
        OwnerStockMovementListView.as_view(),
        name="owner-stock-movements",
    ),
    path(
        "owner/production/orders/",
        OwnerProductionOrderListView.as_view(),
        name="owner-production-orders",
    ),
    path(
        "owner/costing/periods/",
        OwnerCostingPeriodListView.as_view(),
        name="owner-costing-periods",
    ),
    path(
        "schema/",
        SpectacularAPIView.as_view(
            authentication_classes=DOCS_AUTHENTICATION_CLASSES,
            permission_classes=[IsAdminUser],
        ),
        name="schema",
    ),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(
            url_name="api:schema",
            authentication_classes=DOCS_AUTHENTICATION_CLASSES,
            permission_classes=[IsAdminUser],
        ),
        name="docs",
    ),
    path(
        "redoc/",
        SpectacularRedocView.as_view(
            url_name="api:schema",
            authentication_classes=DOCS_AUTHENTICATION_CLASSES,
            permission_classes=[IsAdminUser],
        ),
        name="redoc",
    ),
]
