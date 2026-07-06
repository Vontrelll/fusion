import logging

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.utils.decorators import method_decorator
from django.views.generic.edit import FormView

from .ratelimit import rate_limit

logger = logging.getLogger(__name__)


@method_decorator(rate_limit('password_reset', limit=5, period=3600), name='dispatch')
class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    """Password reset with per-IP rate limiting to reduce abuse."""

    def form_valid(self, form):
        email = form.cleaned_data.get('email', '')
        matching_users = sum(1 for _ in form.get_users(email))
        logger.info(
            'password_reset backend=%s matching_users=%d',
            settings.EMAIL_BACKEND,
            matching_users,
        )

        domain = getattr(settings, 'PASSWORD_RESET_DOMAIN', None)
        if domain:
            opts = {
                'use_https': self.request.is_secure() or not settings.DEBUG,
                'token_generator': self.token_generator,
                'from_email': self.from_email,
                'email_template_name': self.email_template_name,
                'subject_template_name': self.subject_template_name,
                'request': self.request,
                'html_email_template_name': self.html_email_template_name,
                'extra_email_context': self.extra_email_context,
                'domain_override': domain,
            }
            form.save(**opts)
            return FormView.form_valid(self, form)
        return super().form_valid(form)


@method_decorator(rate_limit('password_reset_confirm', limit=10, period=3600), name='dispatch')
class RateLimitedPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Password reset confirm with per-IP rate limiting."""