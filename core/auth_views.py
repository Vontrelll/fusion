from django.contrib.auth import views as auth_views
from django.utils.decorators import method_decorator

from .ratelimit import rate_limit


@method_decorator(rate_limit('password_reset', limit=5, period=3600), name='dispatch')
class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    """Password reset with per-IP rate limiting to reduce abuse."""


@method_decorator(rate_limit('password_reset_confirm', limit=10, period=3600), name='dispatch')
class RateLimitedPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Password reset confirm with per-IP rate limiting."""