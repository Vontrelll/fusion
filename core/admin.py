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


@admin.register(AccountDeletionLog)
class AccountDeletionLogAdmin(admin.ModelAdmin):
    """Immutable audit trail — read-only in admin."""
    list_display = ('user_id', 'username', 'role', 'deleted_at', 'ip_address')
    list_filter = ('role', 'deleted_at')
    search_fields = ('username',)
    readonly_fields = ('user_id', 'username', 'role', 'deleted_at', 'ip_address')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Kid)
class KidAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'family', 'parent', 'date_of_birth')
    search_fields = ('first_name', 'last_name')
    raw_id_fields = ('family', 'parent')


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'timezone', 'data_consent_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__email')
    raw_id_fields = ('user', 'family')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('name', 'family', 'start_time', 'created_by')
    search_fields = ('name',)
    raw_id_fields = ('family', 'created_by')


admin.site.register(Family)
admin.site.register(Invite)
admin.site.register(Team)
admin.site.register(TeamMembership)
admin.site.register(PlayerRegistration)
admin.site.register(Organization)
admin.site.register(TeamEventInvitation)
admin.site.register(TeamEvent)
admin.site.register(TeamEventAttendance)
admin.site.register(Notification)
admin.site.register(RosterRequestKid)