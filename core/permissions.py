"""Permission checking utilities for the Manufacturing ERP.

Centralizes authorization logic so all entry points (services, views, API, admin)
enforce the same permission requirements.
"""

from django.core.exceptions import ValidationError


def require_permission(actor, perm, action_label=None):
    """Raise ValidationError if actor lacks the required permission.

    Args:
        actor: User instance (must be authenticated).
        perm: Permission string in format "app_label.permission_codename".
        action_label: Optional Arabic label for the action (used in error message).

    Returns:
        True if authorized.

    Raises:
        ValidationError: If actor is None, not authenticated, or lacks permission.
    """
    if not actor or not actor.is_authenticated:
        raise ValidationError("يتطلب هذا الإجراء مستخدمًا مصادقًا.")
    if not actor.has_perm(perm):
        label = action_label or perm
        raise ValidationError(f"ليس لديك صلاحية {label}.")
    return True


def require_staff(actor, action_label=None):
    """Legacy helper: require authenticated staff user.

    Kept for backward compatibility; new code should use require_permission.
    """
    if not actor or not actor.is_authenticated or not actor.is_staff:
        raise ValidationError(action_label or "هذا الإجراء متاح للمستخدمين المصرح لهم فقط.")
    return True