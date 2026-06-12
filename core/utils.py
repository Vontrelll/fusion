import logging

from .models import Profile


# =============================================================================
# SAFE PROFILE / ROLE / FAMILY HELPERS
# =============================================================================
# These are the central place for safely accessing the current user's Profile,
# role, and family. Both views and templates (via context processor) should
# use these instead of touching request.user.profile directly.
#
# Why they exist:
# - Prevent crashes when a Profile row is missing (admin-created users,
#   createsuperuser, shell, tests, old data, etc.).
# - Centralize the "owners can never have a family" business rule.
# - One place to make the behavior more robust over time.
#
# The TimezoneMiddleware also creates Profiles defensively, but these helpers
# are deliberately extra safe so we don't depend on middleware alone.
# =============================================================================

def _safe_get_user_profile(request):
    """
    Return the current authenticated user's Profile object (or None).

    Usage:
        profile = _safe_get_user_profile(request)
        if profile:
            ...

    How the call works (common point of confusion):
    - You pass the whole `request` object to the function.
    - Inside, it reads `request.user` and does `request.user.profile`.
    - It returns a normal Profile model instance.
    - `profile = _safe_get_user_profile(request)` just assigns the returned
      object to a local variable. After this line, `profile` is a regular
      Profile with .role, .family, .user, .timezone, etc. The function is done.

    Robustness features:
    - Safe for unauthenticated requests (returns None).
    - If the Profile row is missing, creates one defensively (logs a warning).
    - Never lets a profile lookup crash the request if we can avoid it.
    """
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return None

    try:
        return user.profile
    except Profile.DoesNotExist:
        # Defensive creation for users who never got a Profile
        # (createsuperuser, Django admin, tests, data issues, etc.).
        # We still default to America/Chicago here because we have no browser
        # context. Normal signups go through the form which shows a visible
        # timezone dropdown + JS auto-detection so the user can see and correct it.
        profile = Profile.objects.create(
            user=user,
            role='parent',           # Safe default. Owner accounts created
                                     # outside normal signup should be fixed
                                     # in admin or via proper flows.
            timezone='America/Chicago'
        )
        logger = logging.getLogger(__name__)
        logger.warning(
            "Defensive Profile creation for user=%s (no Profile row existed). "
            "This usually means the user was created via admin, createsuperuser, "
            "shell, or tests instead of the normal signup form.",
            getattr(user, 'username', user)
        )
        return profile
    except Exception:
        logging.getLogger(__name__).exception(
            "Unexpected error fetching Profile for user=%s",
            getattr(user, 'username', user)
        )
        return None


def _safe_get_user_family(request):
    """
    Return the user's Family (or None), with owner enforcement applied.

    Owners are not allowed to belong to a family (families are only for the
    parent/kid side of the app). If an owner ever has a family attached,
    this function clears it and returns None.

    This rule is also enforced in:
    - Profile.save()
    - TimezoneMiddleware (indirectly)
    - The context processor
    """
    profile = _safe_get_user_profile(request)
    if not profile:
        return None

    if profile.role == 'owner' and profile.family is not None:
        profile.family = None
        profile.save(update_fields=['family'])
        return None

    return profile.family


def _get_user_role(request):
    """
    Return the current user's role string: 'parent', 'owner', or None.

    Convenient for quick checks:
        if _get_user_role(request) != "owner":
            ...
    """
    profile = _safe_get_user_profile(request)
    return profile.role if profile else None
