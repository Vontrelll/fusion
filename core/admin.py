from django.contrib import admin
from .models import (
    Event,
    Family,
    Kid,
    Invite,
    Notification,
    Organization,
    PlayerRegistration,
    Profile,
    Team,
    TeamEvent,
    TeamEventAttendance,
    TeamEventInvitation,
    TeamMembership,
    RosterRequestKid,
    AccountDeletionLog,
)

admin.site.register(Event)
admin.site.register(Kid)
admin.site.register(Family)
admin.site.register(Invite)
admin.site.register(Team)
admin.site.register(TeamMembership)
admin.site.register(PlayerRegistration)
admin.site.register(Organization)
admin.site.register(Profile)
admin.site.register(TeamEventInvitation)
admin.site.register(TeamEvent)
admin.site.register(TeamEventAttendance)
admin.site.register(Notification)
admin.site.register(RosterRequestKid)
admin.site.register(AccountDeletionLog)

