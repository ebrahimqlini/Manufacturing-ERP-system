from django.urls import path

from . import views

app_name = "manufacturing"
urlpatterns = [
    path("", views.order_list, name="orders"),
    path("orders/add/", views.order_create, name="order_add"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/edit/", views.order_edit, name="order_edit"),
    path("orders/<int:pk>/release/", views.order_release, name="order_release"),
    path("orders/<int:pk>/complete/", views.order_complete, name="order_complete"),
    path("orders/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/issues/add/", views.material_issue_form, name="issue_add"),
    path("orders/<int:pk>/progress/add/", views.operation_progress_form, name="progress_add"),
    path("orders/<int:pk>/operations/<int:operation_pk>/complete/", views.operation_finish, name="operation_complete"),
    path("orders/<int:pk>/operations/<int:operation_pk>/skip/", views.operation_skip, name="operation_skip"),
    path("orders/<int:pk>/results/add/", views.production_result_form, name="result_add"),
    path("orders/<int:pk>/results/<int:result_pk>/waste/", views.production_material_waste_form, name="result_waste"),
    path("orders/<int:pk>/results/<int:result_pk>/waste-review/", views.confirm_production_waste_review, name="result_waste_review"),
    path("orders/<int:pk>/results/<int:result_pk>/correct/", views.production_result_correction_form, name="result_correct"),
    path("orders/<int:pk>/results/<int:result_pk>/waste/<int:waste_pk>/reverse/", views.production_waste_reversal_form, name="result_waste_reverse"),
    path("orders/<int:pk>/receipts/add/", views.finished_receipt_form, name="receipt_add"),
    path("orders/<int:pk>/receipts/<uuid:receipt_pk>/replace/", views.finished_receipt_replacement_form, name="receipt_replace"),
    path("orders/<int:pk>/counter/start/", views.counter_start_form, name="counter_start"),
    path("counter/<int:pk>/close/", views.counter_close_form, name="counter_close"),
    path("counter/<int:pk>/approve/", views.counter_approve, name="counter_approve"),
    path("counter/<int:pk>/correct/", views.counter_correct_form, name="counter_correct"),
    path("configuration/", views.configuration, name="configuration"),
    path("configuration/lines/add/", views.line_form, name="line_add"),
    path("configuration/lines/<int:pk>/edit/", views.line_form, name="line_edit"),
    path("configuration/operations/add/", views.operation_form, name="operation_add"),
    path("configuration/operations/<int:pk>/edit/", views.operation_form, name="operation_edit"),
    path("configuration/boms/add/", views.bom_form, name="bom_add"),
    path("configuration/boms/<int:pk>/edit/", views.bom_form, name="bom_edit"),
    path("configuration/routings/add/", views.routing_form, name="routing_add"),
    path("configuration/routings/<int:pk>/edit/", views.routing_form, name="routing_edit"),
]
