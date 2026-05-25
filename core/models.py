from django.db import models
from django.conf import settings
from django.forms import CharField
from django.conf import settings
import pytz

# Create your models here.
GENDER_CHOICES = [("M", "Male"), ("F", "Female")]

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    
    ROLE_CHOICES = [
        ('parent', 'Parent / Family'),
        ('owner', 'Team Owner / Organization'),
    ]
    
    role = models.CharField(
        max_length=20, 
        choices=ROLE_CHOICES, 
        default='parent'
    )
    
    timezone = models.CharField(max_length=50, default='America/Chicago')

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"



class Event(models.Model):
    name = models.CharField(max_length=100)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length = 100)
    description = models.TextField(blank=True, null=True)
    kid_attending = models.ForeignKey("Kid", on_delete=models.CASCADE, null=True, blank=True, related_name = 'events')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    family = models.ForeignKey("Family", on_delete=models.CASCADE, related_name= 'events', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
  


    def __str__(self):
        return f"{self.name} for {self.kid_attending}"
    
    class Meta:
        ordering = ['start_time']



class TeamEventInvitation(models.Model):
    team_event = models.ForeignKey('TeamEvent', on_delete=models.CASCADE, related_name='invitations')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='team_invitations')
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('accepted', 'Accepted'),
            ('declined', 'Declined')
        ],
        default='pending'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)   # ← Added

    class Meta:
        unique_together = ('team_event', 'user')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.team_event.name} ({self.status})"

class TeamEvent(models.Model):
    """Master event created by coaches/teams"""
    name = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='team_events')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.team.name}"


class TeamEventAttendance(models.Model):
    """Links a kid to a TeamEvent when parent accepts"""
    team_event = models.ForeignKey('TeamEvent', on_delete=models.CASCADE, related_name='attendances')
    kid = models.ForeignKey('Kid', on_delete=models.CASCADE)
    status = models.CharField(
        max_length=20,
        choices=[('accepted', 'Accepted'), ('declined', 'Declined')],
        default='accepted'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team_event', 'kid')

    def __str__(self):
        return f"{self.kid} attending {self.team_event.name}"



class Kid(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(choices = GENDER_CHOICES, max_length=1)
    family = models.ForeignKey("Family", on_delete=models.CASCADE, related_name='kids', null=True, blank=True)
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name= 'kids')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"






class Family(models.Model):
    family_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    parents = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name= 'families')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name_plural = 'families'

    def __str__(self):
        return self.family_name





class Invite(models.Model):
    family = models.ForeignKey('Family', on_delete=models.CASCADE, related_name='invites', null=True, blank=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_invites') #THE PERSON WHO SENT THE INVITE
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_invites')   #THE PERSON BEING INVITED
    team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='invites', null=True, blank=True) #USED FOR INVITING PARENTS TO TEAMS.
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined')
    ], default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    invite_type = models.CharField(max_length=20, choices=[
    ('family_join_request', 'Family_Join Request'),     #
    ('family_sent_invite', 'Family_Sent Invite'),
    ('team_join_request', 'Team_Join_Request'),
    ('team_sent_invite', 'Team_Sent_Invite')
], default='family_join_request')
    

class Meta:
        unique_together = [
            ('family', 'receiver'),   #PREVENTS DUPLICATE PARENT INVITES 
            ('team', 'receiver'),      # Prevent duplicate team invites
        ]


class Organization(models.Model):
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='owned_organizations'
    )
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']





class Team(models.Model):
    name = models.CharField(max_length=200)
    sport_type = models.CharField(max_length=100, choices=[
        ('basketball', 'Basketball'),
        ('soccer', 'Soccer'),
        ('baseball', 'Baseball'),
        ('football', 'Football'),
        ('volleyball', 'Volleyball'),
        ('hockey', 'Hockey'),
        ('other', 'Other'),
    ])
    
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    organization = models.ForeignKey('Organization', on_delete=models.CASCADE, related_name='teams')

    def __str__(self):
        return f"{self.name} ({self.organization.name})"

    class Meta:
        ordering = ['name']



class TeamMembership(models.Model): #ThROUGH MODEL, ACTS AS A JOIN TABLE TO CONNECT USERS TO EACH TEAM. HELPS ESTABLISH ROLES FOR USERS ON EACH TEAM, PARENT/COACH CAN BE PART OF MULTIPLE TEAMS
    team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='team_memberships')
    
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('parent', 'Parent'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    
    jersey_number = models.CharField(max_length=10, blank=True, null=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team', 'user')

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()} in {self.team.name}"

    
class PlayerRegistration(models.Model):
    team_membership = models.ForeignKey('TeamMembership', on_delete=models.CASCADE, related_name='players')
    kid = models.ForeignKey('Kid', on_delete=models.CASCADE)
    jersey_number = models.CharField(max_length=10, blank=True, null=True)
    position = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=50, default='general')  # e.g. 'team_event_canceled', 'invitation'
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} - {self.user.username}"





class GoogleToken(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='google_token')
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True, null=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Google Token for {self.user.username}"
