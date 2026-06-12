from .utils import _safe_get_user_profile, _get_user_role, _safe_get_user_family


def unread_notifications(request):
    """
    Context processor: adds variables to *every* template automatically.

    This is the template-side equivalent of the safe helpers in core/utils.py.

    Provided variables (available in any template without views passing them):
        - unread_notifications_count
        - current_role          ('parent', 'owner', or 'parent' fallback)
        - current_family        (Family object or None; owners always get None)

    We use the shared _safe_* helpers so:
    - Behavior is identical to what views see.
    - Defensive profile creation works here too.
    - The "owners never have a family" rule is enforced in one place.
    """
    # Always try the safe helpers first (they handle unauthenticated + missing profiles)
    profile = _safe_get_user_profile(request)
    current_role = _get_user_role(request) or 'parent'
    current_family = _safe_get_user_family(request)

    # Notification count is best-effort
    try:
        count = request.user.notifications.filter(is_read=False).count() if request.user.is_authenticated else 0
    except Exception:
        count = 0

    return {
        'unread_notifications_count': count,
        'current_role': current_role,
        'current_family': current_family,
    }
