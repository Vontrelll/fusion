from django.contrib import admin

from core.views import team_invite

# Register your models here.
from .models import Event, Family, Kid, Invite, PlayerRegistration, Team, TeamMembership, PlayerRegistration, TeamInvite


admin.site.register(Event)
admin.site.register(Kid)
admin.site.register(Family)
admin.site.register(Invite)
admin.site.register(Team)
admin.site.register(TeamMembership)
admin.site.register(PlayerRegistration)
admin.site.register(TeamInvite)
