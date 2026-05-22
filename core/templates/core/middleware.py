from django.utils import timezone
from core.models import Profile

class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                profile = Profile.objects.get(user=request.user)
                timezone.activate(profile.timezone)
            except Profile.DoesNotExist:
                timezone.activate('America/Chicago')  # fallback
        else:
            timezone.deactivate()

        return self.get_response(request)