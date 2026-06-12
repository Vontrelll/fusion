from django.utils import timezone
from core.models import Profile

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