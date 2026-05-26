from django.shortcuts import render, redirect
from oauthlib.oauth2.rfc6749.errors import LoginRequired
from .models import Kid, Event, Family, Invite, PlayerRegistration, Team, TeamEventInvitation, TeamMembership, Profile, Organization, TeamEvent, TeamEventAttendance, Notification 
from django.http import HttpResponse 
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm, User
from django.contrib.auth import update_session_auth_hash
from core.forms import CustomUserCreationForm, OrganizationForm, TeamEventForm
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from .models import GoogleToken
from django.conf import settings
import os 
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'   # Allow HTTP for local testing
from . forms import TeamForm


# Create your views here.
# core/views.py


@login_required
def dashboard(request):
    profile = request.user.profile   # This works because of OneToOne
    if profile.role != "parent":
        return redirect('owner_dashboard')
    
    else:
        # Get all families the user belongs to
        user_families = request.user.families.all()

        kids = request.user.kids.all()

        now = timezone.now()
        today = timezone.localdate()


        # Proper today range (fixes the off-by-one day bug)

        todays_events = Event.objects.filter(
        family__in=user_families,
        start_time__date=today   # This is the simplest and most reliable way
    ).order_by('start_time')

        print(f"Today's events found: {todays_events.count()}")
        for event in todays_events:
            print(f" - {event.name} | {event.start_time} | {event.start_time.date()}")

        # This week's events (family-wide)
        week_ago = now - timedelta(days=7)
        this_weeks_events = Event.objects.filter(
            family__in=user_families,
            start_time__gte=week_ago
        ).order_by('start_time')

        context = {
            "num_of_kids": kids.count(),
            "num_of_events": todays_events.count(),
            "weeks_events_count": this_weeks_events.count(),
            "user_events": todays_events,
            "kids": kids,
    }

    return render(request, "core/dashboard.html", context)

@login_required
def owner_dashboard(request):
    # Role check - only allow owners
    profile = request.user.profile
    if profile.role != "owner":
        messages.error(request, "You do not have access to the Owner Dashboard.")
        return redirect('dashboard')  # or 'parent_dashboard' if you rename it

    # Get user's organization (assuming one for now)
    organization = Organization.objects.filter(owner=request.user).first()
    
    # Get teams under that organization
    teams = Team.objects.filter(organization=organization) if organization else []

    pending_requests = Invite.objects.filter(receiver=request.user,status="pending").count()

    context = {
        'user_organization': organization,
        'total_teams': len(teams),
        'total_players': 0,           # TODO: Update later with real player count
        'pending_requests': pending_requests,  
        'my_teams': teams,
    }
    
    return render(request, "core/owner_dashboard.html", context)

    

#This section below is for all things Events related 
# Creating a new event. Connects to add_event.html
#THI ADD EVENT IS FOR FAMILIES ONLY
@login_required
def add_event(request):
    if request.user.profile.role != "parent":
        messages.error(request, "Only parents can create family events.")
        return redirect('owner_dashboard')

    user_kids = request.user.kids.all()
    user_families = request.user.families.all()

    if request.method == "POST":
        name = request.POST.get("name")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        location = request.POST.get("location")
        kid_id = request.POST.get('kid')
        family_id = request.POST.get('family')

        try:
            kid = Kid.objects.get(id=kid_id)
        except Kid.DoesNotExist:
            messages.error(request, "Invalid kid selected.")
            return redirect('add_event')

        family = Family.objects.get(id=family_id, parents=request.user) if family_id else user_families.first()

        try:
            start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
            end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        except ValueError:
            messages.error(request, "Invalid date or time format.")
            return redirect('add_event')
        
        if start_time < timezone.now():
            messages.error(request, "Start time cannot be in the past.")
            return redirect('add_event')

        if start_time > end_time:
            messages.error(request, "End time must be after start time.")
            return redirect('add_event')

        # Create event object (not saved yet)
        event = Event(
            name=name,
            start_time=start_time,
            end_time=end_time,
            location=location,
            kid_attending=kid,
            created_by=request.user,
            family=family
        )

        # === CONFLICT CHECK ===
        if has_conflict(event, kid=kid):
            messages.error(request, "Time conflict detected. This slot overlaps with an existing event or team event.")
            return redirect('add_event')

        event.save()
        messages.success(request, "Event created successfully!")
        return redirect("event_list")

    return render(request, "core/add_event.html", {'kids': user_kids, 'families': user_families})


@login_required
def add_team_event(request):
    if request.user.profile.role != "owner":
        messages.error(request, "Only team owners can create team events.")
        return redirect('dashboard')

    if request.method == "POST":
        form = TeamEventForm(request.POST, user=request.user)
        if form.is_valid():
            team_event = form.save(commit=False)

            if team_event.start_time < timezone.now():
                messages.error(request, "Start time cannot be in the past.")
                return redirect('add_team_event')

            if team_event.start_time > team_event.end_time:
                messages.error(request, "End time must be after start time.")
                return redirect('add_team_event')

            team_event.created_by = request.user

            # === CONFLICT CHECK FOR TEAM ===
            if has_conflict(team_event, team=team_event.team):
                messages.error(request, "Time conflict detected. This team already has an event at this time.")
                return redirect('add_team_event')

            team_event.save()

            # Create invitations...
            memberships = TeamMembership.objects.filter(
                team=team_event.team,
                role='parent'
            ).select_related('user')

            count = 0
            for membership in memberships:
                _, created = TeamEventInvitation.objects.get_or_create(
                    team_event=team_event,
                    user=membership.user,
                    defaults={'status': 'pending'}
                )
                if created:
                    count += 1

            messages.success(request, f"Team event '{team_event.name}' created and {count} parents invited.")
            return redirect('event_list')
        else:
            messages.error(request, "Form has errors.")
    else:
        form = TeamEventForm(user=request.user)

    return render(request, 'core/add_team_event.html', {'form': form})





#EDIT TEAM EVENTS 
@login_required
def edit_event(request, event_id):

    if request.user.profile.role != "parent":
        messages.error(request, 'you do not have permission to edit this event.')
        redirect('owner_dashboard')

    #GAIN ACCESS TO THE EVENT BEING EDITED
    try:
        event = Event.objects.get(id= event_id)
    except Event.DoesNotExist:
        messages.error(request, "Event not found.")
        return redirect('event_list')
    
    kids = []
    team = None
        
    #CHECKING IF EVENT HAS FAMILY BECAUSE THAT MEANS ITS A FAMILY EVENT 
    if event.family:
        kids = Kid.objects.filter(family=event.family)

        #CHECKS IF USER HAS PERMISSION TO EDIT THIS EVENT
        if not request.user.families.filter(id=event.family.id).exists():
            messages.error(request, "You do not have permission to modify this event.")
            return redirect('event_list')

    
    elif event.team:
        team = event.team
        if request.user != event.team.organization.owner:
            messages.error(request, "You do not have permission to modify this event.")
            return redirect('event_list')
        

    #GATHERING INFO USER WANTS TO UPDATE
    if request.method == "POST":
        event.name = request.POST.get("name")
        event.location = request.POST.get("location")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")

        if event.family:
            kid_id = request.POST.get("kid")
            if kid_id:
                try:
                    kid = Kid.objects.get(id=kid_id)
                    event.kid_attending = kid
                except Kid.DoesNotExist:
                    messages.error(request, 'This kid does not exist.')
                    return redirect('edit_event')
            

        #CONVERTING STRINGS INTO TIMEZONE-AWARE DATEANDTIME
        try:
            event.start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
            event.end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        except ValueError:
            messages.error(request, "Invalid date/time format.")
            return redirect('edit_event', event_id=event_id)

        if has_conflict(event):
            messages.error(request, "Time conflict detected. This slot overlaps with an existing event. Please choose a different time.")
            return redirect('edit_event', event_id=event_id) 
        
        event.save()
        messages.success(request, 'Event update successful.')
        return redirect("event_list")
            
    return render(request, "core/edit_event.html", {"event": event,
    "kids": kids, "team": team})


def edit_team_event(request, event_id):
    try:
        team_event= TeamEvent.objects.get(id=event_id)
        
    except TeamEvent.DoesNotExist:
        messages.error(request, "This team event does not exist.")
        return redirect('event_list')
    
    if request.user.profile.role != "owner":
        messages.error(request, 'you do not have permission to edit this event.')
        return redirect('dashboard')
    
    user_teams = Team.objects.filter(organization__owner=request.user)

    if request.method == "POST":
        name = request.POST.get('name')
        start_time_str = request.POST.get('start_time')
        end_time_str = request.POST.get('end_time')
        location = request.POST.get('location')
        description = request.POST.get('description')
        team_id = request.POST.get('team')

        try:
            team = Team.objects.get(id=team_id)
        except Team.DoesNotExist:
            messages.error(request, 'Selected team does not exist.')
            return redirect('edit_team_event', event_id=event_id)

        #CONVERTING STRINGS INTO TIMEZONE-AWARE DATEANDTIME
        try:
            start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
            end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        except ValueError:
            messages.error(request, "Invalid date/time format.")
            return redirect('edit_team_event', event_id=event_id)
        
        if start_time < timezone.now():
            messages.error(request, "Start time cannot be in the past.")
            return redirect('edit_team_event', event_id=event_id)

        if start_time > end_time:
            messages.error(request, "End time must be after start time.")
            return redirect('add_team_event')

        team_event.name = name
        team_event.start_time = start_time
        team_event.end_time = end_time
        team_event.location = location
        team_event.description = description
        team_event.team = team

        if has_conflict(team_event, team=team):
            messages.error(request, "Time conflict detected with another team event.")
            return redirect('edit_event', event_id=event_id) 

        team_event.save()
        messages.success(request, "Team event updated successfully.")
        
        #SEND NOTIFICATIONS TO PARENTS WHO ACCEPTED THE INVITE 
        parents_attendances = TeamEventAttendance.objects.filter(team_event= team_event, status= 'accepted').select_related('kid')
        notified_users = set()

        for attendance in parents_attendances:
            user = attendance.kid.parent
            if user.id in notified_users:
                continue
        
            title = f"Updated: {team_event.name}"
            message = (f"The organizer updated '{team_event.name}'. Please review the new details and check if it still fits your schedule.")
            Notification.objects.create(user=user, title=title, message=message, notification_type='team_event_updated', extra_data={'team_event_id': team_event.id})
            notified_users.add(user.id)

        return redirect('event_list')

    context = {"event": team_event, "teams": user_teams }

    return render(request, "core/edit_team_event.html", context)


@login_required
def review_team_event_update(request, event_id):
    try:
        team_event = TeamEvent.objects.get(id=event_id)
        
        attendance = TeamEventAttendance.objects.filter(
            team_event=team_event,
            kid__parent=request.user,
            status='accepted'
        ).select_related('kid').first()

        if not attendance:
            messages.error(request, "You are not attending this event.")
            return redirect('event_list')

        chosen_kid = attendance.kid

        # Improved conflict check
        conflicting_events = Event.objects.filter(
            created_by=request.user,
            kid_attending=chosen_kid,
            start_time__lt=team_event.end_time,
            end_time__gt=team_event.start_time,
        ).exclude(id=team_event.id)  # just in case

        print("Debug - Team Event Time:", team_event.start_time, "-", team_event.end_time)
        print("Debug - Found conflicts:", conflicting_events.count())

        context = {
            'event': team_event,
            'conflicting_events': conflicting_events,
            'chosen_kid': chosen_kid,
        }
        
        return render(request, 'core/review_team_event_update.html', context)

    except TeamEvent.DoesNotExist:
        messages.error(request, "This event no longer exists.")
        return redirect('event_list')



@login_required
def keep_team_event_update(request, event_id):
    """Parent chooses to keep the updated team event"""
    try:
        event = TeamEvent.objects.get(id=event_id)
        
        # Mark the notification as read (optional but recommended)
        Notification.objects.filter(
            user=request.user,
            notification_type='team_event_updated'
        ).update(is_read=True)
        
        messages.success(request, f"You kept '{event.name}'. It will remain on your calendar with the updated details.")
        
    except TeamEvent.DoesNotExist:
        messages.error(request, "This event no longer exists.")
    
    return redirect('event_list')


@login_required
def remove_team_event_attendance(request, event_id):
    """Parent chooses to remove the team event from their calendar"""
    try:
        event = TeamEvent.objects.get(id=event_id)
        
        # Remove the attendance record
        attendance = TeamEventAttendance.objects.filter(
            team_event=event,
            kid__parent=request.user
        ).first()
        
        if attendance:
            attendance.delete()
            messages.success(request, f"'{event.name}' has been removed from your calendar.")
        else:
            messages.error(request, "You were not attending this event.")
            
    except TeamEvent.DoesNotExist:
        messages.error(request, "This event no longer exists.")
    
    return redirect('event_list')
        

        



def has_conflict(new_event, kid=None, team=None):
    if not new_event.start_time or not new_event.end_time:
        return False

    # === PARENT CREATING PERSONAL EVENT ===
    if kid:
        # Check personal events
        personal_events = Event.objects.filter(
            created_by=new_event.created_by if hasattr(new_event, 'created_by') else None,
            kid_attending=kid
        )
        for event in personal_events:
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
                return True

        # Check accepted Team Events
        team_events = TeamEvent.objects.filter(
            attendances__kid=kid,
            attendances__status='accepted'
        )
        for event in team_events:
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
                return True

    # === OWNER CREATING TEAM EVENT ===
    if team:
        existing_team_events = TeamEvent.objects.filter(team=team)
        for event in existing_team_events:
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
                if getattr(new_event, 'id', None) != event.id:
                    return True

    return False





@login_required
def delete_event(request, event_id=None, attendance_id=None, team_event_id=None):
    """Unified delete view for:
    - Personal/Family Events
    - Parent removing themselves from a Team Event
    - Owner deleting a Team Event for everyone
    """

    # ==================== CASE 1: Personal / Family Event ====================
    if event_id:
        try:
            event = Event.objects.get(id=event_id)
        except Event.DoesNotExist:
            messages.error(request, "Event not found.")
            return redirect('event_list')

        # Permission check
        if event.family and not request.user.families.filter(id=event.family.id).exists():
            messages.error(request, "You do not have permission to delete this event.")
            return redirect('event_list')

        if request.method == "POST":
            event_name = event.name
            event.delete()
            messages.success(request, f"Event '{event_name}' deleted successfully.")
            return redirect("event_list")

        return render(request, "core/delete_event.html", {
            "event": event,
            "is_personal_event": True
        })

    # ==================== CASE 2: Parent opting out of Team Event ====================
    elif attendance_id:
        try:
            attendance = TeamEventAttendance.objects.get(id=attendance_id, kid__parent=request.user)
        except TeamEventAttendance.DoesNotExist:
            messages.error(request, "Event not found.")
            return redirect('event_list')

        if request.method == "POST":
            event_name = attendance.team_event.name
            attendance.delete()
            messages.success(request, f"You have been removed from '{event_name}'. This event will no longer appear in your calendar.")
            return redirect("event_list")

        return render(request, "core/delete_event.html", {
            "event": attendance.team_event,
            "is_team_attendance": True
        })

    # ==================== CASE 3: Owner deleting full Team Event ====================
    elif team_event_id:
        try:
            team_event = TeamEvent.objects.get(id=team_event_id)
        except TeamEvent.DoesNotExist:
            messages.error(request, "Team event not found.")
            return redirect('event_list')

        if request.user != team_event.team.organization.owner:
            messages.error(request, "You do not have permission to delete this team event.")
            return redirect('event_list')

        if request.method == "POST":
            event_name = team_event.name
            team_name = team_event.team.name
            org_name = team_event.team.organization.name

            # Notify all affected parents
            attendances = TeamEventAttendance.objects.filter(
                team_event=team_event,
                status='accepted'
            ).select_related('kid__parent')

            for attendance in attendances:
                parent = attendance.kid.parent
                Notification.objects.create(
                    user=parent,
                    title="Team Event Canceled",
                    message=f"The event '{event_name}' for {team_name} ({org_name}) has been canceled by the organizer.",
                    notification_type='team_event_canceled'
                )

            # Delete all attendances and the team event
            attendances.delete()
            team_event.delete()

            messages.success(request, f"Team event '{event_name}' has been deleted and parents have been notified.")
            return redirect("event_list")

        return render(request, "core/delete_event.html", {
            "event": team_event,
            "is_team_event": True
        })

    # Invalid request
    messages.error(request, "Invalid request.")
    return redirect('event_list')



@login_required
def notifications(request):
    pending_requests = Invite.objects.filter(receiver=request.user, status="pending").order_by("-created_at")
    
    team_event_invitations = TeamEventInvitation.objects.filter(
        user=request.user, status="pending"
    ).select_related('team_event', 'team_event__team').order_by("-created_at")

    # New: Canceled team events
    canceled_notifications = Notification.objects.filter(
        user=request.user,
        notification_type='team_event_canceled'
    ).order_by('-created_at')

    return render(request, "core/notifications.html", {
        'pending_requests': pending_requests,
        'team_event_invitations': team_event_invitations,
        'canceled_notifications': canceled_notifications,   # ← Add this
    })

@login_required
def event_list(request):
    if request.user.profile.role == "parent":
        user_families = request.user.families.all()

        # Personal Events
        personal_events = list(Event.objects.filter(
            family__in=user_families
        ).select_related('kid_attending', 'family', 'created_by'))

        # Accepted Team Events
        accepted_team_events = list(TeamEvent.objects.filter(
            attendances__kid__parent=request.user,
            attendances__status='accepted'
        ).select_related('team'))

        # Combine both
        all_events = personal_events + accepted_team_events
        all_events.sort(key=lambda x: x.start_time)

        # Attach attendance_id for team events (for delete button)
        for event in all_events:
            if hasattr(event, 'attendances'):  # This is a TeamEvent
                attendance = event.attendances.filter(
                    kid__parent=request.user,
                    status='accepted'
                ).first()
                if attendance:
                    event.attendance_id = attendance.id

        context = {
            'events': all_events,
            'is_parent': True,
        }

    else:  # Owner
        events = TeamEvent.objects.filter(
            team__organization__owner=request.user
        ).select_related('team').order_by('start_time')
        
        context = {'events': events, 'is_parent': False}

    return render(request, 'core/event_list.html', context)



#This section below is the AUTHENTICATION section 

def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(username=username, password=password)

        #This is not compelte code only a placeholder for the skeleton. would still 
        #need to redirect etc.
        if user:
            login(request, user)
            return redirect("dashboard")

        #This is not compelte code only a placeholder for the skeleton. I would 
        #need to setup real error messages re rendering etc.

        else:
            return render(request, "core/login.html", context={
                "error": "Invalid credentials, try again"
            })

    return render(request, "core/login.html")

@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


def signup_view(request):
    if request.method == "POST":
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()                    # This already creates the Profile
            login(request, user)
            
            if user.profile.role == 'owner':
                return redirect('owner_dashboard')
            else:
                return redirect('setup_family')
    else:
        form = CustomUserCreationForm()

    return render(request, "core/signup.html", {'form': form})


@login_required
def account_settings(request):
    if request.method == "POST":
        # Update user info
        request.user.first_name = request.POST.get('first_name', request.user.first_name)
        request.user.last_name = request.POST.get('last_name', request.user.last_name)
        request.user.email = request.POST.get('email', request.user.email)
        request.user.save()
        
        messages.success(request, "Profile updated successfully!")
        return redirect('account_settings')

    return render(request, "core/account_settings.html", {
        'user': request.user
    })


@login_required
def change_password(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)   # Important: keeps user logged in
            messages.success(request, "Your password was successfully updated!")
            return redirect('settings')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "core/change_password.html", {'form': form})


@login_required
def google_login(request):
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uris": ["http://127.0.0.1:8000/accounts/google/login/callback/"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=['https://www.googleapis.com/auth/calendar.readonly']
    )
    flow.redirect_uri = "http://127.0.0.1:8000/accounts/google/login/callback/"
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    request.session['google_oauth_state'] = state
    return redirect(authorization_url)


@login_required
def google_callback(request):
    state = request.session.pop('google_oauth_state', None)
    if not state:
        messages.error(request, "Session expired. Please try again.")
        return redirect('dashboard')

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uris": ["http://127.0.0.1:8000/accounts/google/login/callback/"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=['https://www.googleapis.com/auth/calendar.readonly'],
        state=state
    )
    flow.redirect_uri = "http://127.0.0.1:8000/accounts/google/login/callback/"

    try:
        # This line is the most important fix
        authorization_response = request.build_absolute_uri()
        flow.fetch_token(authorization_response=authorization_response)

        credentials = flow.credentials

        GoogleToken.objects.update_or_create(
            user=request.user,
            defaults={
                'access_token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'expires_at': timezone.now() + timedelta(seconds=credentials.expires_in or 3600),
            }
        )

        messages.success(request, "✅ Google Calendar connected successfully!")
        return redirect('dashboard')

    except Exception as e:
        messages.error(request, f"Failed to connect Google Calendar: {str(e)}")
        return redirect('dashboard')





    #This section below is logic for creating/managing kids

@login_required
def add_kid(request):
    if request.method == "POST":
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        date_of_birth = request.POST.get("date_of_birth")
        gender = request.POST.get("gender")
        user_family = request.user.families.first()   # Get the first family the user belongs to

        #CHECK IF USER IS PART OF A FAMILY
        if not user_family:
            messages.error(request, "You must be part of a family before adding kids.")
            return redirect('setup_family')
        #CHECK IF USER IS PART OF THE FAMILY THE KID IS BEING ADDED TO
        if user_family not in request.user.families.all():
            messages.error(request, "You do not have permission to add kids to this family.")
            return redirect('family_list')
            
   
        #creating the kid and saving it to the parent and database
        kid = Kid(first_name=first_name, last_name=last_name, date_of_birth=date_of_birth, 
        gender=gender)
        kid.parent = request.user
        kid.family = user_family
        kid.save()
        return redirect('kid_list')

    #GET request
    return render(request, "core/add_kid.html")



@login_required
def edit_kid(request, kid_id):
    kid = Kid.objects.get(id= kid_id)

    if request.user not in kid.family.parents.all():
        messages.error(request, "You do not have permission to edit this kid.")
        return redirect('kid_list')

    if request.method == "POST":
        kid.first_name = request.POST.get("first_name")
        kid.last_name = request.POST.get("last_name")
        kid.date_of_birth = request.POST.get("date_of_birth")
        kid.gender = request.POST.get("gender")
        kid.save()
        messages.success(request, "Kid information updated successfully.")
        return redirect('kid_list')
    
    return render(request, "core/edit_kid.html")

@login_required
def delete_kid(request, kid_id):
    kid = Kid.objects.get(id=kid_id)

    if request.user not in kid.family.parents.all():
        messages.error(request, "You do not have permission to delete this kid.")
        return redirect('kid_list')
    if request.method == "POST":
        kid.delete()
        messages.success(request, "Kid deleted successfully.")
        return redirect('kid_list')

    
    return render(request, "core/delete_kid.html", context= {"kid": kid})



@login_required
def kid_list(request):
    kids = Kid.objects.filter(parent=request.user)
    return render(request,"core/kid_list.html",context= {"kids": kids})


#From here down is family views
@login_required
def add_family(request):
    if request.method == "POST":
        family_name = request.POST.get("family_name")
        new_family = Family(family_name= family_name)
        new_family.save()
        new_family.parents.add(request.user)
        return redirect('dashboard')
    
    return render(request, "core/add_family.html")

#Family List
@login_required
def family_list(request):
    families = request.user.families.all()
    return render(request, "core/family_list.html", context={"families": families})

#Create first Family
@login_required
def create_first_family(request):
    # If user already has a family, redirect to dashboard
    if request.user.families.exists():
        return redirect('dashboard')
    
    if request.method == "POST":
        family_name = request.POST.get("family_name")
        new_family = Family(family_name=family_name)
        new_family.created_by = request.user
        new_family.save()
        new_family.parents.add(request.user)
        return redirect('dashboard')
    
    return render(request, "core/create_first_family.html")

#SETUP FAMILY
def setup_family(request):
    return render(request, "core/setup_family.html")

#JOIN FAMILY LOFGIC FOR SIGNUP FLOW
@login_required
def join_family(request):
    if request.method == "POST":
        username = request.POST.get("username")
        family_id = request.POST.get("family_id") #FAMILY ID IS WHEN A USER HAS MORE THAN
                                                  #ONE FAMILY THAT CAN BE JOINED. A DROP DOWN WILL 
                                                  #APPEAR AND FAMILY ID WILL BE LOGGED
        try:
            target_user = User.objects.get(username=username)
            if family_id:
                family = Family.objects.get(id=family_id)
                invite = Invite.objects.create(sender=request.user, receiver=target_user, family=family, status="pending", invite_type="join_request")
                return redirect('dashboard')
                
            count = target_user.families.count()
            if count == 0:
                messages.error(request, f"This user does not have a family")
    

            elif count == 1:
                family = target_user.families.first()
                if request.user in family.parents.all():
                    messages.error(request, f"You are already a member of the {family.family_name} family")
                    
                else:
                    invite= Invite.objects.create(sender=request.user, receiver=target_user, family=family, status="pending", invite_type="join_request")
                    messages.success(request, "Your family request was sent successfully")
                    return redirect('dashboard')

            else:
                list_of_families = target_user.families.all()
                return render(request, "core/join_family.html", { 
            "families": list_of_families,
            "target_user": target_user,
            "show_family_choice": True})
        
        except User.DoesNotExist:
            messages.error(request, "This user does not exist")
            return render(request, "core/join_family.html")
            
    return render(request, "core/join_family.html")




#ALL THINGS INVITE RELATED. FOR PARENT TO PARENT INVITE/REQUEST AS WELL AS TEAM TO PARENT AND PARENT TO TEAM INVITE/REQUEST
@login_required
def invite_parent(request, family_id):
    
    if request.method == "POST":
        username = request.POST.get("username")

        try:
            target_user = User.objects.get(username=username)
            family = Family.objects.get(id=family_id)
            
            if target_user == request.user:
                messages.error(request, "You are already a member of this family!")

            elif target_user in family.parents.all():
                messages.error(request, f"{target_user.username} is already a member of the family")

            elif Invite.objects.filter(family=family, receiver=target_user, status="pending"):
                messages.error(request, "An invite has already been sent")
                return redirect('invite_parent', family_id=family_id)

            else:
                invite= Invite.objects.create(sender=request.user, receiver=target_user, family=family, status="pending", invite_type="sent_invite")
                messages.success(request, "Your family request was sent successfully")
                return redirect('dashboard')
        

        except User.DoesNotExist:
            messages.error(request, "User does not exist")
    
    return render(request, "core/invite_parent.html")

@login_required
def team_invite_to_parent(request, team_id, username):
    try:
        team = Team.objects.get(id= team_id)
        target_user= User.objects.get(username__iexact=username)
    except User.DoesNotExist:
        messages.error(request, "User does not exist.")
        return redirect('team_to_parent_invite_search', team_id=team_id)

    
    # PERMISSION CHECK
    if request.user.profile.role != "owner":
        messages.error(request, "You are not authorized to send this invite.")
        return redirect('dashboard')

        
    #CHECK IF THE TARGET USER IS ALREADY A MEMBER OF THE TEAM
    if TeamMembership.objects.filter(team=team, user=target_user).exists():
        messages.error(request, f"{target_user} is already a member of this team.")
        return redirect('team_to_parent_invite_search')


    #CHECK IF AN INVITE ALREADY EXIST
    if Invite.objects.filter(team=team, receiver=target_user).exclude(status="declined").exists():
        messages.error(request, "An invite has already been sent to this user.")
        return redirect('team_to_parent_invite_search', team_id=team_id)

    # CREATE THE INVITE
    Invite.objects.create(
        team=team,
        sender=request.user,
        receiver=target_user,
        invite_type="team_sent_invite",
        status="pending"
    )

    messages.success(request, f"Invite sent to {username} successfully!")
    return redirect('owner_dashboard')


@login_required
def parent_to_team_request(request, team_id):
    try:
        team = Team.objects.get(id=team_id)

    except Team.DoesNotExist:
        messages.error(request, "Team does not exist.")
        return redirect('parent_to_team_request', team_id=team_id)

    if request.user.profile.role != "parent":
        messages.error(request, "You are not authorized to send this request")
        return redirect('owner_dashboard')


    if request.method == "POST":

        if TeamMembership.objects.filter(team=team, user=request.user).exists():
            messages.error(request, 'you are already a member of this team.')
            return redirect('find_teams')

        if Invite.objects.filter(team=team, sender=request.user).exclude(status="declined").exists():
            messages.error(request, 'You are already a member of this team.')
            return redirect('find_teams')

# Create the join request
        Invite.objects.create(
            team=team,
            sender=request.user,
            receiver=team.organization.owner, 
            invite_type="team_join_request",
            status="pending")
        messages.success(request, 'Request successfully sent')
        return redirect('find_teams')


    return render(request, "core/parent_to_team_request.html", {"team": team})
        
        
 



@login_required
def remove_parent(request, family_id, parent_id,):
    parent = User.objects.get(id=parent_id)
    family = Family.objects.get(id=family_id)
    if family.created_by != request.user:
        messages.error(request, f"You are not authorized to remove a {parent}")
        return redirect('family_list')
    if parent == family.created_by:
        messages.error(request, "You can not delete the creator of the family.")
        return redirect('family_list')
    if request.method == "POST":
        family.parents.remove(parent) 
        Invite.objects.filter(family=family, receiver=parent).delete()
        return redirect('family_list')

    return render(request, "core/remove_parent.html", context={"parent": parent})

@login_required
def family_detail(request, family_id):
    family = Family.objects.get(id=family_id)

    return render(request, "core/family_details.html", context= {"family":family})


@login_required
def accept_invite(request, invite_id):
    invite = Invite.objects.get(id=invite_id)

    if invite.receiver != request.user:
        messages.error(request, "You are not authorized to accept this invite.")
        return redirect('dashboard')

    # Handle different invite types
    if invite.invite_type == "join_request":
        invite.family.parents.add(invite.sender)

    elif invite.invite_type == "sent_invite":
        invite.family.parents.add(invite.receiver)

    elif invite.invite_type == "team_join_request":
        membership, created = TeamMembership.objects.get_or_create(
            team=invite.team,
            user=invite.sender,
            defaults={'role': 'parent'})
        return redirect('select_kids_for_team_roster', invite_id=invite.id)

    
    elif invite.invite_type == "team_sent_invite":
        # Correct way using get_or_create
        membership, created = TeamMembership.objects.get_or_create(
            team=invite.team,
            user=invite.receiver,
            defaults={'role': 'parent'})
        return redirect('select_kids_for_team_roster', invite_id=invite.id)


    else:
        # Unknown invite type - safety net
        messages.error(request, "Invalid invite type.")
        return redirect('dashboard')

    # Only run this if we successfully handled the invite
    invite.status = "accepted"
    invite.save()
    messages.success(request, "Invite accepted successfully!")
    return redirect('dashboard')



@login_required
def decline_invite(request, invite_id):
    invite = Invite.objects.get(id=invite_id)

    if invite.receiver != request.user:
        messages.error(request, "You are not authorized to decline this invite.")
        return redirect('dashboard')
    

    invite.delete()
    messages.success(request, "Invite has been declined.")
    return redirect('dashboard')

@login_required
def notifications(request):
    pending_requests = Invite.objects.filter(
        receiver=request.user,
        status="pending"
    ).order_by("-created_at")

    team_event_invitations = TeamEventInvitation.objects.filter(
        user=request.user,
        status="pending"
    ).select_related('team_event', 'team_event__team').order_by("-created_at")

    # Canceled team events
    canceled_notifications = Notification.objects.filter(
        user=request.user,
        notification_type='team_event_canceled'
    ).order_by('-created_at')

    # ✅ NEW: Updated Team Events
    updated_notifications = Notification.objects.filter(
        user=request.user,
        notification_type='team_event_updated',
        is_read=False                     # Only show unread ones
    ).order_by('-created_at')

    return render(request, "core/notifications.html", {
        'pending_requests': pending_requests,
        'team_event_invitations': team_event_invitations,
        'canceled_notifications': canceled_notifications,
        'updated_notifications': updated_notifications,   # ← Add this
    })


#THE SECTION BELOW IS FOR ALL THINGS TEAM/ORGANIZATION RELATED

@login_required
def create_team(request):
        #CHECKS IF USER HAS AN ORG BECAUSE THEY ARE REQUIRED TO HAVE ONE BEFORE CREATING A TEAM
    if not Organization.objects.filter(owner=request.user).exists():
        messages.error(request, "You must have an Organization before you can create a team.")
        return redirect('create_organization')

        #RUNS IF USER HAS AN ORG
    elif request.method == "POST":
        form = TeamForm(request.POST)
         
        #FORM CHECK IF PASS, CREATES AND SAVES TEAM
        if form.is_valid():
            team = form.save(commit=False)
            organization = Organization.objects.get(owner=request.user) #BE SURE THAT TEAMS OWNERS ONLY HAVE ONE ORG
            team.organization = organization #THIS CREATES THE CONNECTION TO THE OWNERS ORG
            team.save()

            # TeamMembership acts as a join table between User and Team.
            # It defines the relationship + role (admin, parent, etc.) 
            # and allows one user to be part of multiple teams.
            membership = TeamMembership(team=team, user=request.user, role='admin') 
            membership.save()
            messages.success(request, f"Team '{team.name}' created successfully!")
            return redirect('owner_dashboard')
    else:
        form = TeamForm()

    return render(request, "core/create_team.html", {"form":form})


@login_required
def team_list(request): 
    teams = Team.objects.filter(organization__owner= request.user).order_by('name')

    # ← Pass this flag that lets teamlist know its present in order to send a parent invite. user has to select team first
    from_invite = request.GET.get('from') == 'invite_parent'

    context = {
        'teams': teams,
        'from_invite': from_invite,          
    }

    return render(request, "core/team_list.html", context)

@login_required
def find_teams(request):
    query = request.GET.get('q', '').strip()
    
    if query:
        teams = Team.objects.filter(name__icontains=query).order_by('name')
    else:
        teams = Team.objects.all().order_by('name')

    # Annotate each team with membership status
    for team in teams:
        team.user_is_member = TeamMembership.objects.filter(
            team=team, 
            user=request.user
        ).exists()
        
        team.user_has_pending_request = Invite.objects.filter(
            team=team, 
            sender=request.user, 
            status="pending"
        ).exists()

    return render(request, "core/find_teams.html", {
        "teams": teams,
        "query": query
    })


@login_required
def team_to_parent_invite_search(request, team_id):
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        messages.error(request, "Team not found.")
        return redirect('owner_dashboard')

    # Only team owner / admin can invite
    if request.user != team.organization.owner:
        messages.error(request, "You do not have permission to invite to this team.")
        return redirect('owner_dashboard')

    query = request.GET.get('q', '').strip()
    results = []

    if query:
        # Search users by username (case insensitive)
        results = User.objects.filter(username__icontains=query).exclude(id=request.user.id)[:20]

    context = {
        'team': team,
        'query': query,
        'results': results,
    }
    return render(request, 'core/team_to_parent_invite_search.html', context)


def select_kids_for_team_roster(request, invite_id):
    invite = Invite.objects.get(id=invite_id)

    # Authorization check
    if invite.receiver != request.user:
        messages.error(request, "You are not authorized to select kids for this invite.")
        return redirect('dashboard')

    try:
        membership = TeamMembership.objects.get(user=request.user, team=invite.team)
    except TeamMembership.DoesNotExist:
        messages.error(request, "Team membership not found.")
        return redirect('dashboard')

    kids = Kid.objects.filter(parent=request.user)

    if request.method == "POST":
        selected_kid_ids = request.POST.getlist('kids')

        if not selected_kid_ids:
            messages.error(request, "Please select at least one kid.")
        else:
            for kid_id in selected_kid_ids:
                kid = Kid.objects.get(id=kid_id)
                PlayerRegistration.objects.create(
                    team_membership=membership, 
                    kid=kid
                )

            # Mark the invite as accepted
            invite.status = "accepted"
            invite.save()

            messages.success(request, "Kids successfully added to the team!")
            return redirect('dashboard')

    # GET request - show the form
    context = {
        'invite': invite,
        'kids': kids,
        'team': invite.team,
    }
    return render(request, "core/select_kids_for_team_roster.html", context)

@login_required
def decline_team_event_invite(request, team_event_invitation_id):
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    # Authorization check
    if invitation.user != request.user:
        messages.error(request, "You are not authorized to decline this invite.")
        return redirect('dashboard')

    invitation.delete()
    messages.success(request, "Invite has been declined.")
    return redirect('notifications')


@login_required
def team_event_kid_selection(request, team_event_invitation_id):
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
        team_event = invitation.team_event
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    if invitation.user != request.user:
        messages.error(request, "Unauthorized.")
        return redirect('notifications')

    kids = Kid.objects.filter(
        playerregistration__team_membership__team=team_event.team,
        parent=request.user
    ).distinct()

    if request.method == "POST":
        selected_kid_ids = request.POST.getlist('kids')
        if not selected_kid_ids:
            messages.error(request, "Please select at least one kid.")
            return render(request, "core/team_event_kid_selection.html", {
                'invitation': invitation,
                'team_event': team_event,
                'kids': kids
            })

        kid_id = selected_kid_ids[0]
        try:
            kid = Kid.objects.get(id=kid_id)
            kid_full_name = f"{kid.first_name} {kid.last_name}".strip()
        except Kid.DoesNotExist:
            messages.error(request, "Selected kid not found.")
            return redirect('notifications')

        # Check for conflict
        conflicting_events = Event.objects.filter(
            created_by=request.user,
            kid_attending=kid,
            start_time__lt=team_event.end_time,
            end_time__gt=team_event.start_time,
        )

        if conflicting_events.exists():
            request.session['selected_kid_id'] = kid_id
            return redirect('resolve_team_event_conflict', team_event_invitation_id=team_event_invitation_id)
        else:
            # No conflict → Create Attendance
            TeamEventAttendance.objects.create(
                team_event=team_event,
                kid=kid,
                status='accepted'
            )
            invitation.status = "accepted"
            invitation.save()
            messages.success(request, f"Event added successfully for {kid_full_name}!")
            return redirect('event_list')

    context = {
        'invitation': invitation,
        'team_event': team_event,
        'kids': kids,
    }
    return render(request, "core/team_event_kid_selection.html", context)

@login_required
def resolve_team_event_conflict(request, team_event_invitation_id):
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
        team_event = invitation.team_event
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    if invitation.user != request.user:
        messages.error(request, "You are not authorized.")
        return redirect('notifications')

    # Get the kid that was selected
    kid_id = request.session.get('selected_kid_id')
    chosen_kid = None
    if kid_id:
        try:
            chosen_kid = Kid.objects.get(id=kid_id, parent=request.user)
        except Kid.DoesNotExist:
            pass

    # Find conflicting personal events for this kid
    conflicting_event = None
    if chosen_kid:
        conflicting_event = Event.objects.filter(
            created_by=request.user,
            kid_attending=chosen_kid,
            start_time__lt=team_event.end_time,
            end_time__gt=team_event.start_time,
        ).first()

    context = {
        'invitation': invitation,
        'team_event': team_event,
        'conflicting_event': conflicting_event,
        'chosen_kid': chosen_kid,
    }
    return render(request, 'core/resolve_team_event_conflict.html', context)

@login_required
def replace_with_team_event(request, team_event_invitation_id):
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
        team_event = invitation.team_event
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    if invitation.user != request.user:
        messages.error(request, "Unauthorized.")
        return redirect('notifications')

    kid_id = request.session.get('selected_kid_id')
    if not kid_id:
        messages.error(request, "Kid selection missing.")
        return redirect('notifications')

    try:
        chosen_kid = Kid.objects.get(id=kid_id, parent=request.user)
        kid_full_name = f"{chosen_kid.first_name} {chosen_kid.last_name}".strip()
    except Kid.DoesNotExist:
        messages.error(request, "Invalid kid.")
        return redirect('notifications')

    # Delete conflicting personal events
    conflicting_events = Event.objects.filter(
        created_by=request.user,
        kid_attending=chosen_kid,
        start_time__lt=team_event.end_time,
        end_time__gt=team_event.start_time,
    )
    deleted_count = conflicting_events.count()
    conflicting_events.delete()

    # Create attendance instead of new Event
    TeamEventAttendance.objects.create(
        team_event=team_event,
        kid=chosen_kid,
        status='accepted'
    )

    # Cleanup
    if 'selected_kid_id' in request.session:
        del request.session['selected_kid_id']

    invitation.status = "accepted"
    invitation.save()

    if deleted_count > 0:
        messages.success(request, f"Replaced {deleted_count} conflicting event(s) for {kid_full_name}.")
    else:
        messages.success(request, f"Team event added successfully for {kid_full_name}.")

    return redirect('event_list')






@login_required
def create_organization(request):
    if Organization.objects.filter(owner=request.user).exists():
        messages.error("You already have an organization.")
        return redirect('owner_dashboard')
    if request.method == "POST":
        form = OrganizationForm(request.POST)
        if form.is_valid():
            organization = form.save(commit=False)
            organization.owner = request.user          # Important: Set current user as owner
            organization.save()
            
            messages.success(request, f"Organization '{organization.name}' created successfully!")
            return redirect('owner_dashboard')         # or 'create_team' later
    else:
        form = OrganizationForm()

    return render(request, "core/create_organization.html", {"form": form})

def organization_details(request, org_id):
    try:
        organization = Organization.objects.get(id=org_id, owner=request.user)
    except Organization.DoesNotExist:
        messages.error(request, "Organization not found.")
        return redirect('owner_dashboard')

    context = {
        'organization': organization,
        # Add more context later (teams, members, etc.)
    }
    return render(request, 'core/organization_details.html', context)


   




    
    




    
            
