from django.shortcuts import render, redirect
from oauthlib.oauth2.rfc6749.errors import LoginRequired
from .models import Kid, Event, Family, Invite, Team, TeamMembership, Profile, Organization 
from django.http import HttpResponse 
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm, User
from django.contrib.auth import update_session_auth_hash
from core.forms import CustomUserCreationForm, OrganizationForm
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from .models import GoogleToken
from django.conf import settings
import os 
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'   # Allow HTTP for local testing
from . forms import TeamForm


# Create your views here.


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
    #PERMISSION CHECK TO AVOID OWNERS OR FUTURE ROLES FROM CREATING EVENTS FOR 
    #FAMILIES 
    if request.user.profile.role != "parent":
        messages.error(request, "Only parents can create family events.")
        return redirect('owner_dashboard')

    #GETTING KIDS AND FAMILIES CONNECTED TO THE USER 
    user_kids = request.user.kids.all()
    user_families = request.user.families.all()

    #SUBMITTING THE DATA FOR EVENT TO BE CHECKED BEFORE SAVED 
    if request.method == "POST":
        name = request.POST.get("name")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        location = request.POST.get("location")
        kid_id = request.POST.get('kid')


        #CHECK IF KIDS EXIST
        try:
            kid = Kid.objects.get(id=kid_id) 
        except Kid.DoesNotExist:
            messages.error(request, "Invalid kid selected.")
            return redirect('add_event')

    #THIS CHECK IS FOR PARENTS WITH MULTIPLE FAMILIES. IF NO FAMILIY IS SELECTED
    #THE DEFAULT IS THE FIRST FAMILY
        family_id = request.POST.get('family')
        if family_id:
            family = Family.objects.get(id=family_id, parents=request.user)
        else:
            family = user_families.first()

        try:
            #Converting strings to timezone-aware datetime
            start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
            end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))

        except ValueError:
            messages.error(request, "Invalid date or time format. Please try again.")
            return redirect('add_event')
        
        #CREATING THE EVENT
        event = Event(name=name, start_time=start_time, end_time=end_time, 
        location=location, kid_attending=kid, created_by= request.user, family=family)
        
        #Conflict Detection 
        if has_conflict(event):
            messages.error(request, "Time conflict detected. This slot overlaps with an existing event. Please choose a different time.")
            return redirect('add_event')

        #No conflict --> event saved 
        event.save() 
        return redirect("event_list")

    #GET request 
    return render(request, "core/add_event.html", {'kids': user_kids, 'families': user_families})


#THIS EVENT CREATION IS FOR TEAMS
@login_required
def add_team_event(request):
    #PERMISSION CHECK TO PREVENT ROLES THAT ARE NOT 'OWNER' FROM CREATING EVENTS
    #FOR TEAMS
    if request.user.profile.role != "owner":
        messages.error(request, "Only team owners/coaches can create team events.")
        return redirect('dashboard')
    
    #ACCESS THE TEAMS FOR THE USER
    teams = Team.objects.filter(organization__owner=request.user).order_by('name')

    #PROCESSING THE FORM
    if request.method == "POST":
        name = request.POST.get("name")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        location = request.POST.get("location")
        team_id = request.POST.get('team')

        try:
            team = Team.objects.get(id=team_id)
        except Team.DoesNotExist:
         messages.error(request, "Invalid team selected.")
         return redirect('add_team_event')

        try:
            #Converting strings to timezone-aware datetime
            start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
            end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        except ValueError:
            messages.error(request, "Invalid date or time format. Please try again.")
            return redirect('add_team_event')

        #CREATE THE EVENT FOR THE TEAM 
        event = Event(name=name, start_time=start_time, end_time=end_time, 
        location=location, team=team, created_by= request.user)
        
        #Conflict Detection 
        if has_conflict(event):
            messages.error(request, "Time conflict detected. This slot overlaps with an existing event. Please choose a different time.")
            return redirect('add_team_event')

            #No conflict --> event saved 
        event.save() 
        messages.success(request, 'Event created successfully.')
        return redirect("event_list")
        

    return render(request,"core/add_team_event.html", {"teams": teams})






#EDIT FAMILY AND TEAM EVENTS 
@login_required
def edit_event(request, event_id):

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

#Delete List
@login_required
def delete_event(request, event_id):
    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
         messages.error(request, "Event not found.")
         return redirect('event_list')
    
    if event.family:

        #CHECKS IF USER HAS PERMISSION TO EDIT THIS EVENT
         #CHECKS IF USER HAS PERMISSION TO EDIT THIS EVENT
        if not request.user.families.filter(id=event.family.id).exists():
            messages.error(request, "You do not have permission to delete this event.")
            return redirect('event_list')

    if event.team:
        if request.user != event.team.organization.owner:
            messages.error(request, "You do not have permission to delete this event.")
            return redirect('event_list')

    if request.method == "POST":
        event.delete()
        messages.success(request, "Event deleted successfully.")
        return redirect("event_list")

    return render(request, "core/delete_event.html", {"event": event})



#Event list 
@login_required
def event_list(request):

    if request.user.profile.role == "parent":
        user_families = request.user.families.all()
        events = Event.objects.filter(family__in=user_families).order_by('start_time')
    
    elif request.user.profile.role == "owner":
        events = Event.objects.filter(team__organization__owner= request.user).order_by('start_time')


    else:
        events = Event.objects.none()   # fallback

    # One single return at the end
    return render(request, "core/event_list.html", {
        "events": events,})

#Conflict Detection 
def has_conflict(new_event):
    if new_event.family:
        family_events = new_event.family.events.all()
        for event in family_events:
            if event.id == new_event.id:
                continue 
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time) and (new_event.kid_attending == event.kid_attending):
                return True

        return False 

    elif new_event.team:
        team_events = new_event.team.events.all()

        for event in team_events:
            if event.id == new_event.id:
                continue 
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
                return True
        
        return False





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
        TeamMembership.objects.create(
            team=invite.team,
            user=invite.sender,
            role="parent"
        )

    elif invite.invite_type == "team_sent_invite":
        TeamMembership.objects.create(
            team=invite.team,
            user=invite.receiver,
            role="parent"
        )

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
    pending_requests = Invite.objects.filter(receiver=request.user, status="pending",).order_by("-created_at")

    return render(request, "core/notifications.html", {
        'pending_requests': pending_requests
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


   




    
    




    
            
