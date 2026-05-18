from http.client import HTTPResponse
import re
from django.shortcuts import render, redirect
from .models import Kid, Event, Family, Invite, Team, TeamInvite, TeamMembership 
from django.contrib.auth.models import User
from django.http import HttpResponse 
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from core.forms import CustomUserCreationForm
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
    # Get all families the user belongs to
    user_families = request.user.families.all()

    kids = request.user.kids.all()

    now = timezone.now()

    # Proper today range (fixes the off-by-one day bug)
    today_start = timezone.make_aware(
    timezone.datetime.combine(now.date(), timezone.datetime.min.time())
)
    today_end = today_start + timedelta(days=1)

    todays_events = Event.objects.filter(
    family__in=user_families,
    start_time__gte=today_start,
    start_time__lt=today_end
).order_by('start_time')

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
    

#This section below is for all things Events related 
# Creating a new event. Connects to add_event.html
@login_required
def add_event(request):
    if request.method == "POST":
        name = request.POST.get("name")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        location = request.POST.get("location")
        kid_id = request.POST.get('kid')
        kid = Kid.objects.get(id=kid_id) #Fetching the Kid
        user_family = request.user.families.first()

        #PERMISSIONS CHECK BEFORE CREATING EVENT TO PREVENT CREATING EVENTS FOR FAMILIES USER 
        #IS NOT A MEMBER 
        if user_family not in request.user.families.all():
            messages.error(request, "You do not have permission to add events to this family.")
            return redirect('dashboard')
        

        #Converting strings to timezone-aware datetime
        start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
        end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        event = Event(name=name, start_time=start_time, end_time=end_time, 
        location=location, kid_attending=kid)
        event.created_by= request.user
        event.family = user_family
        
        #Conflict Detection 
        if has_conflict(event):
            return HttpResponse("Conflict detected! Event was not added")

        #No conflict --> event saved 
        event.save() 
        return redirect("event_list")

    #GET request 
    kids = Kid.objects.all()
    return render(request, "core/add_event.html", {'kids': kids})
            

#Edit Event
@login_required
def edit_event(request, event_id):
    event = Event.objects.get(id= event_id)

        #PERMISSIONS CHECK BEFORE CREATING EVENT TO PREVENT CREATING EVENTS FOR FAMILIES USER 
        #IS NOT A MEMBER 
    if event.family not in request.user.families.all():
        messages.error(request, "You do not have permission to modify this event.")
        return redirect('event_list')

    
    if request.method == "POST":
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
        end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        event.name = request.POST.get("name")
        event.location = request.POST.get("location")
        event.start_time = start_time
        event.end_time = end_time



        kid_id = request.POST.get('kid')
        kid = Kid.objects.get(id=kid_id) 
        event.kid_attending = kid

        if has_conflict(event):
            messages.error(request, "Conflict detected! Event was not updated.")
        
        event.save()
        return redirect("event_list")

    kids = Kid.objects.filter(family= event.family)
    return render(request, "core/edit_event.html", {"event": event,
    "kids": kids
    })

#Delete List
@login_required
def delete_event(request, event_id):
    event = Event.objects.get(id=event_id)
    if event.family not in request.user.families.all():
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
    user_families = request.user.families.all()
    events = Event.objects.filter(family__in=user_families).order_by('start_time')
    return render(request, "core/event_list.html", context= {"events": events})

#Conflict Detection 
def has_conflict(new_event):
    for event in new_event.family.events.all():
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
            user = form.save()
            login(request, user)
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
                    invite= Invite.objects.create(sender=request.user, requester=target_user, family=family, status="pending", invite_type="join_request")
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




#Invite Parent to Family
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

            elif Invite.objects.filter(family=family, receiver= target_user, status="pending"):
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

    #RUNS WHEN CREATOR OF FAMILY SENDS INVITE 
    if invite.invite_type == "sent_invite" and invite.receiver == request.user:
        invite.family.parents.add(invite.receiver)
        invite.status = "accepted"
        invite.save()
        messages.success(request, "You have been added to the family!")
        return redirect('dashboard')

        #RUNS WHEN CREATOR OF FAMILY RECEIVES INVITE 
    elif invite.invite_type == "join_request" and invite.receiver == request.user:
        invite.family.parents.add(invite.sender)
        invite.status = "accepted"
        invite.save()
        messages.success(request, f"{invite.requester.username} has been added to the family!")
        return redirect('dashboard')
    
    else:
        messages.error(request, "You are not authorized to accept this invite.")
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


#THE SECTION BELOW IS FOR ALL THINGS TEAM RELATED

@login_required
def create_team(request):
    if request.method == "POST":
        form = TeamForm(request.POST)

        if form.is_valid():
            team = form.save(commit=False)
            team.owner = request.user
            team.save()

            membership = TeamMembership(team=team, user=request.user, role='admin')
            membership.save()
            messages.success(request, f"Team '{team.name}' created successfully!")
            return redirect('dashboard')

    else:
        form = TeamForm()

    return render(request, "core/create_team.html", {"form":form})


@login_required
def team_list(request):
    teams = request.user.owned_teams.all()
    return render(request, "core/team_list.html", {"teams": teams})

@login_required
def find_teams(request):
    query = request.GET.get('q', '')        # Read search term from URL

    if query:
        teams = Team.objects.filter(name__icontains=query).order_by('name')
        print("Found teams:", teams)   # Debug line
    else:
        teams = Team.objects.none()         #Shows no teams on page load

    return render(request, "core/find_teams.html", {"teams": teams})


@login_required
def team_invite(request, team_id):
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        messages.error(request, "Team not found.")
        return redirect('find_teams')

    # Check if user is already a member
    if TeamMembership.objects.filter(team=team, user=request.user).exists():
        messages.warning(request, "You are already a member of this team.")
        return redirect('find_teams')

    # Check if user already has a pending request
    if TeamInvite.objects.filter(team=team, user=request.user, status='pending').exists():
        messages.warning(request, "You have already sent a request to join this team.")
        return redirect('find_teams')

    #Check if user owns team being requested 
    if team.owner == request.user:
        messages.error(request, "You cannot request to join your own team.")
        return redirect('find_team')

    # Create the join request
    TeamInvite.objects.create(
        team=team,
        user=request.user,
        status='pending'
    )

    messages.success(request, f"Request to join '{team.name}' has been sent!")
    return redirect('find_teams')



   




    
    




    
            
