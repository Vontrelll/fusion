from django.conf import settings
from django.utils import timezone
from core.models import Profile


class SecurityHeadersMiddleware:
    """Add defense-in-depth HTTP headers (CSP, Referrer-Policy, Permissions-Policy)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(settings, 'SECURITY_CSP', None):
            response['Content-Security-Policy'] = settings.SECURITY_CSP
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        return response


class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                profile = Profile.objects.get(user=request.user)
            except Profile.DoesNotExist:
                # Auto-create a default profile to prevent RelatedObjectDoesNotExist errors
                # (e.g. for users created directly in admin or shell without going through signup form)
                profile = Profile.objects.create(
                    user=request.user,
                    role='parent',  # safe default; owner can be changed in admin or via other flows
                    timezone='America/Chicago'  # Defensive only; real signups use the visible dropdown + JS detection on the signup form so users see/correct the value
                )
            timezone.activate(profile.timezone)
        else:
            timezone.deactivate()

        return self.get_response(request)