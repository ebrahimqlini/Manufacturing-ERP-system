from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import generics, status
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from catalog.models import Product
from sales.models import SalesOrder
from sales.services import submit_customer_order_request

from .dashboard import (
    costing_payload,
    dashboard_payload,
    inventory_payload,
    production_payload,
    purchases_payload,
    sales_payload,
)
from .pagination import StandardResultsSetPagination
from .permissions import IsCustomerUser, IsOwnerUser
from .serializers import (
    CustomerOrderDetailSerializer,
    CustomerOrderListSerializer,
    CustomerOrderRequestSerializer,
    CustomerProductSerializer,
    CustomerProfileSerializer,
    DashboardQuerySerializer,
    DashboardResponseSerializer,
    TokenResponseSerializer,
)


def _validated_dashboard_query(request):
    serializer = DashboardQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _customer_products():
    return Product.objects.filter(
        active=True,
        available_to_customers=True,
    ).select_related("category", "base_uom", "selling_price_info__unit").prefetch_related(
        "unit_conversions__unit"
    )


class CustomerAPIView(APIView):
    permission_classes = [IsAuthenticated, IsCustomerUser]

    def get_customer(self):
        return self.request.user.customer_account


class CustomerProfileView(CustomerAPIView):
    @extend_schema(responses={200: CustomerProfileSerializer})
    def get(self, request):
        return Response(CustomerProfileSerializer(self.get_customer()).data)


class CustomerProductListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsCustomerUser]
    serializer_class = CustomerProductSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = _customer_products()
        category = self.request.query_params.get("category", "").strip()
        if category:
            queryset = queryset.filter(category__code=category)
        query = self.request.query_params.get("q", "").strip()
        if query:
            queryset = queryset.filter(sku__icontains=query) | queryset.filter(name_ar__icontains=query)
        return queryset.order_by("sku")


class CustomerProductDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsCustomerUser]
    serializer_class = CustomerProductSerializer
    lookup_field = "pk"

    def get_queryset(self):
        return _customer_products()


class CustomerPriceListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsCustomerUser]
    serializer_class = CustomerProductSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return _customer_products().filter(selling_price_info__isnull=False).order_by("sku")


class CustomerOrderListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsCustomerUser]
    serializer_class = CustomerOrderListSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = SalesOrder.objects.filter(
            customer=self.request.user.customer_account
        ).prefetch_related("lines").order_by("-order_date", "-created_at")
        status_filter = self.request.query_params.get("status", "").strip()
        if status_filter:
            if status_filter not in SalesOrder.Status.values:
                raise DRFValidationError({"status": "حالة أمر البيع غير صالحة."})
            queryset = queryset.filter(status=status_filter)
        return queryset


class CustomerOrderDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsCustomerUser]
    serializer_class = CustomerOrderDetailSerializer
    lookup_field = "order_number"
    lookup_url_kwarg = "order_number"

    def get_queryset(self):
        return SalesOrder.objects.filter(
            customer=self.request.user.customer_account
        ).select_related("customer").prefetch_related("lines__product", "lines__unit")


class CustomerOrderRequestView(APIView):
    permission_classes = [IsAuthenticated, IsCustomerUser]

    @extend_schema(
        request=CustomerOrderRequestSerializer,
        responses={201: CustomerOrderDetailSerializer},
        description=(
            "Create a draft sales order request for the authenticated customer. "
            "The order is never confirmed, no stock is reserved or deducted, and no shipment is created."
        ),
    )
    def post(self, request):
        serializer = CustomerOrderRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lines = [
            {
                "product": line["product"],
                "quantity": line["quantity"],
                "unit": line["unit"],
                "notes": line.get("notes", ""),
            }
            for line in data["lines"]
        ]
        try:
            order = submit_customer_order_request(
                actor=request.user,
                lines=lines,
                requested_delivery_date=data.get("requested_delivery_date"),
                customer_reference=data.get("customer_reference", ""),
                notes=data.get("notes", ""),
            )
        except DjangoValidationError as exc:
            detail = getattr(exc, "message_dict", None) or exc.messages
            raise DRFValidationError(detail) from exc
        order = SalesOrder.objects.select_related("customer").prefetch_related(
            "lines__product", "lines__unit"
        ).get(pk=order.pk)
        return Response(CustomerOrderDetailSerializer(order).data, status=status.HTTP_201_CREATED)


class OwnerDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerUser]

    @extend_schema(
        parameters=[DashboardQuerySerializer],
        responses={200: DashboardResponseSerializer},
        description="Owner dashboard snapshot composed from persisted operational and costing data.",
    )
    def get(self, request):
        return Response(dashboard_payload(_validated_dashboard_query(request)))


class OwnerSalesView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerUser]

    @extend_schema(parameters=[DashboardQuerySerializer], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(sales_payload(_validated_dashboard_query(request)))


class OwnerPurchasesView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerUser]

    @extend_schema(parameters=[DashboardQuerySerializer], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(purchases_payload(_validated_dashboard_query(request)))


class OwnerInventoryView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerUser]

    @extend_schema(parameters=[DashboardQuerySerializer], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(inventory_payload(_validated_dashboard_query(request)))


class OwnerProductionView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerUser]

    @extend_schema(parameters=[DashboardQuerySerializer], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(production_payload(_validated_dashboard_query(request)))


class OwnerCostingView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerUser]

    @extend_schema(parameters=[DashboardQuerySerializer], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(costing_payload(_validated_dashboard_query(request)))


class TokenObtainView(ObtainAuthToken):
    """Issue a token from a JSON username/password request."""

    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    throttle_classes = [AnonRateThrottle]

    @extend_schema(
        request=AuthTokenSerializer,
        responses={200: TokenResponseSerializer},
        auth=[],
        description="Exchange active user credentials for an API token. Send JSON only.",
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class TokenLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None}, description="Revoke the current API token.")
    def post(self, request):
        if not isinstance(request.auth, Token):
            return Response({"detail": "Token authentication is required."}, status=status.HTTP_400_BAD_REQUEST)
        request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
