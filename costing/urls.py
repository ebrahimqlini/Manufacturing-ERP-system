from django.urls import path

from . import views

app_name = "costing"

urlpatterns = [
    # Monthly Costing Periods
    path("", views.period_list, name="period_list"),
    path("calculate/", views.period_calculate, name="period_calculate"),
    path("periods/<int:pk>/", views.period_detail, name="period_detail"),
    path("periods/<int:pk>/orders/", views.order_breakdown, name="order_breakdown"),
    path("compare/", views.period_compare, name="period_compare"),
    path("periods/<int:pk>/policies/", views.period_policy_edit, name="period_policy_edit"),
    path("periods/<int:pk>/finalize/", views.period_finalize, name="period_finalize"),
    path("periods/<int:pk>/reopen/", views.period_reopen, name="period_reopen"),
    path("entries/<int:pk>/breakdown/", views.product_breakdown, name="product_breakdown"),

    # Monthly Expenses
    path("expenses/", views.expense_list, name="expense_list"),
    path("expenses/add/", views.expense_create, name="expense_create"),
    path("expenses/<int:pk>/edit/", views.expense_edit, name="expense_edit"),
    path("expenses/<int:pk>/reverse/", views.expense_reverse, name="expense_reverse"),

    # Monthly Profit, Operating Expenses, and Loss Carry-Forward
    path("profit/", views.profit_period_list, name="profit_period_list"),
    path("profit/calculate/", views.profit_period_calculate, name="profit_period_calculate"),
    path("profit/<int:pk>/", views.profit_period_detail, name="profit_period_detail"),
    path("profit/<int:pk>/finalize/", views.profit_period_finalize, name="profit_period_finalize"),
    path("profit/<int:pk>/reopen/", views.profit_period_reopen, name="profit_period_reopen"),

    # Raw Material Prices
    path("prices/", views.price_list, name="price_list"),
    path("prices/add/", views.price_create, name="price_create"),
    path("prices/<int:pk>/edit/", views.price_edit, name="price_edit"),

    # Roll Yield Standards
    path("yield-standards/", views.yield_standard_list, name="yield_standard_list"),
    path("yield-standards/add/", views.yield_standard_create, name="yield_standard_create"),
    path("yield-standards/<int:pk>/edit/", views.yield_standard_edit, name="yield_standard_edit"),
    path("yield-report/", views.yield_report_view, name="yield_report"),

    # Selling Prices (Display-Only)
    path("selling-prices/", views.selling_price_list, name="selling_price_list"),
    path("selling-prices/add/", views.selling_price_form, name="selling_price_add"),
    path("selling-prices/<int:pk>/edit/", views.selling_price_form, name="selling_price_edit"),
]
