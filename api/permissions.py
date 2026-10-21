from rest_framework.permissions import BasePermission


class IsCustomerUser(BasePermission):
    """Allow only active, non-staff users linked to an active customer."""

    message = "هذه الواجهة متاحة لحسابات العملاء المرتبطة فقط."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active or user.is_staff:
            return False
        try:
            customer = user.customer_account
        except AttributeError:
            return False
        return bool(customer and customer.active)


class IsOwnerUser(BasePermission):
    """Owner/staff boundary used by the existing internal ERP screens."""

    message = "هذه الواجهة متاحة لمستخدمي النظام الداخليين فقط."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_staff)
