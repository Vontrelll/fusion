from django.contrib import admin
# Register your models here.
from .models import Event, Family, Kid, Invite, Organization, PlayerRegistration, Profile, Team, TeamMembership, PlayerRegistration
Organization, Profile


admin.site.register(Event)
admin.site.register(Kid)
admin.site.register(Family)
admin.site.register(Invite)
admin.site.register(Team)
admin.site.register(TeamMembership)
admin.site.register(PlayerRegistration)
admin.site.register(Organization)
admin.site.register(Profile)
