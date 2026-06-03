from django.shortcuts import render, redirect
from .models import Kid, Event, Family, Invite, PlayerRegistration, Team, TeamEventInvitation, TeamMembership, Profile, Organization, TeamEvent, TeamEventAttendance, Notification, RosterRequestKid
from django.http import HttpResponse 
from django.utils import timezone
from django.urls import reverse
from datetime import datetime, timedelta
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm, User
from django.contrib.auth import update_session_auth_hash
from core.forms import CustomUserCreationForm, OrganizationForm, TeamEventForm
from . forms import TeamForm
from datetime import date, timedelta
from django.db.models import Q
from collections import defaultdict


# Create your views here.
# core/views.py


@login_required
def dashboard(request):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    profile = request.user.profile   # This works because of OneToOne
    if profile.role != "parent":
        return redirect('owner_dashboard')
    
    else:
        # Get the user's single family
        user_family = request.user.profile.family
        user_families = [user_family] if user_family else []

        kids = Kid.objects.filter(family=user_family) if user_family else Kid.objects.none()

        now = timezone.now()
        today = timezone.localdate()


        # Proper today range (fixes the off-by-one day bug)

        todays_events = Event.objects.filter(
            family__in=user_families,
            start_time__date=today   # This is the simplest and most reliable way
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

@login_required
def owner_dashboard(request):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

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

    user_family = request.user.profile.family
    user_kids = Kid.objects.filter(family=user_family) if user_family else Kid.objects.none()
    family_parents = user_family.parents.all() if user_family else []

    if request.method == "POST":
        name = request.POST.get("name")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        location = request.POST.get("location")
        kid_ids = request.POST.getlist('kids')
        attending_parent_ids = request.POST.getlist('attending_parents')


        # Validate selected kids belong to the family
        try:
            selected_kids = list(Kid.objects.filter(id__in=kid_ids, family=user_family))
            if len(selected_kids) != len(kid_ids):
                messages.error(request, "Invalid kid selection. Please only select your own kids.")
                return render(request, "core/add_event.html", {
                    'kids': user_kids,
                    'family': user_family,
                    'family_parents': family_parents,
                    'submitted': request.POST
                })
        except Exception:
            messages.error(request, "Error processing kid selection.")
            return render(request, "core/add_event.html", {
                'kids': user_kids,
                'family': user_family,
                'family_parents': family_parents,
                'submitted': request.POST
            })

        # Parse times
        try:
            start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
            end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        except ValueError:
            messages.error(request, "Invalid date or time format.")
            return render(request, "core/add_event.html", {
                'kids': user_kids,
                'family': user_family,
                'family_parents': family_parents,
                'submitted': request.POST
            })


        if start_time > end_time:
            messages.error(request, "End time must be after start time.")
            return render(request, "core/add_event.html", {
                'kids': user_kids,
                'family': user_family,
                'family_parents': family_parents,
                'submitted': request.POST
            })

        # Create event for conflict checking
        event = Event(
            name=name,
            start_time=start_time,
            end_time=end_time,
            location=location,
            created_by=request.user,
            family=user_family
        )

        # Conflict Check
        if has_conflict(event, kids=selected_kids):
            messages.error(request, "Time conflict detected for one or more kids. This slot overlaps with an existing event.")
            return render(request, "core/add_event.html", {
                'kids': user_kids,
                'family': user_family,
                'family_parents': family_parents,
                'submitted': request.POST
            })

        # Save the event
        event.save()
        event.kids.set(selected_kids)

        # Handle attending parents (optional)
        if attending_parent_ids:
            try:
                attending_parents = User.objects.filter(
                    id__in=attending_parent_ids,
                    profile__family=user_family
                )
                event.attending_parents.set(attending_parents)
            except Exception:
                pass  # Optional field

        messages.success(request, "Event created successfully!")
        return redirect("event_list")

    # GET request
    context = {
        'kids': user_kids,
        'family': user_family,
        'family_parents': family_parents,
    }

    return render(request, "core/add_event.html", context)


@login_required
def add_team_event(request):
    if request.user.profile.role != "owner":
        messages.error(request, "Only team owners can create team events.")
        return redirect('dashboard')

    if request.method == "POST":
        form = TeamEventForm(request.POST, user=request.user)
        if form.is_valid():
            team_event = form.save(commit=False)

            now = timezone.now()

            #if team_event.start_time < now:
                #messages.error(request, "Start time cannot be in the past.")
                #return redirect('add_team_event')

            if team_event.start_time > team_event.end_time:
                messages.error(request, "End time must be after start time.")
                return render(request, "core/add_team_event.html", {"form": form})

            team_event.created_by = request.user

            # === CONFLICT CHECK FOR TEAM ===
            if has_conflict(team_event, team=team_event.team):
                messages.error(request, "Time conflict detected. This team already has an event at this time.")
                return render(request, "core/add_team_event.html", {"form": form})

            team_event.save()

            # Create invitations...
            memberships = TeamMembership.objects.filter(
                team=team_event.team,
                role='parent'
            ).select_related('user')

            count = 0
            for membership in memberships:
                invitation, created = TeamEventInvitation.objects.get_or_create(
                    team_event=team_event,
                    user=membership.user,
                    defaults={'status': 'pending'}
                )
                if created:
                    count += 1
                    # Send notification only for newly created invitations (one per parent)
                    # Use get_or_create to strongly prevent any duplicate notifs for same (user, type, event)
                    Notification.objects.get_or_create(
                        user=membership.user,
                        notification_type='team_event_invitation',
                        extra_data__team_event_id=team_event.id,
                        defaults={
                            'title': f"Invitation: {team_event.name}",
                            'message': f"You have been invited to the team event '{team_event.name}' by {team_event.team.name}.",
                            'extra_data': {
                                'team_event_id': team_event.id,
                                'invitation_id': invitation.id,
                                'team_id': team_event.team.id
                            }
                        }
                    )

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
    user_family = request.user.profile.family

    if request.user.profile.role != "parent":
        messages.error(request, 'You do not have permission to edit this event.')
        return redirect('owner_dashboard')

    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        messages.error(request, "Event not found.")
        return redirect('event_list')

    if not user_family or user_family.id != event.family.id:
        messages.error(request, "You do not have permission to modify this event.")
        return redirect('event_list')

    # Initialize for GET and failed POST
    selected_kids = event.kids.all()
    selected_attending_parents = event.attending_parents.all()

    if request.method == "POST":
        name = request.POST.get("name")
        location = request.POST.get("location")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        kid_ids = request.POST.getlist("kids")
        attending_parent_ids = request.POST.getlist("attending_parents")

        # Update basic fields
        if name:
            event.name = name
        if location:
            event.location = location

        # Update kids (allow clearing all)
        selected_kids = Kid.objects.filter(id__in=kid_ids, family=event.family)
        event.kids.set(selected_kids)

        # Update attending parents (allow clearing all)
        selected_attending_parents = User.objects.filter(
            id__in=attending_parent_ids,
            profile__family=event.family
        )
        event.attending_parents.set(selected_attending_parents)

        # Parse times
        try:
            event.start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
            event.end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        except ValueError:
            messages.error(request, "Invalid date/time format.")
            return redirect('edit_event', event_id=event_id)

        # Conflict check
        if has_conflict(event, kids=selected_kids):
            messages.error(request, "Time conflict detected. This slot overlaps with an existing event.")
            return redirect('edit_event', event_id=event_id)

        event.save()
        messages.success(request, 'Event updated successfully.')
        return redirect("event_list")

    # GET request
    context = {
        'event': event,
        'selected_kids': selected_kids,
        'selected_attending_parents': selected_attending_parents,
        'family_kids': user_family.kids.all() if user_family else [],
        'family_parents': user_family.parents.all() if user_family else [],
    }

    return render(request, 'core/edit_event.html', context)



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

        
        #SEND NOTIFICATIONS TO PARENTS WHO ACCEPTED THE INVITE 
        parents_attendances = TeamEventAttendance.objects.filter(team_event= team_event, status= 'accepted').select_related('kid')

        #THIS IS SET SO THAT IN THE NOTIFICATIONS IF THE USER CLICKS ON IT BUT DOES NOTHING THEY CAN COME BACK TO IT LATER 
        parents_attendances.update(needs_review=True)

        #THIS IS TO MAKE SURE USERS ARE NOTIFIED ONCE EVEN IF THEY HAVE MULTIPLE CHILDREN ON THE TEAM
        notified_users = set()


        for attendance in parents_attendances:
            user = attendance.kid.parent
            if user.id in notified_users:
                continue
        
            title = f"Updated: {team_event.name}"
            message = (f"The organizer updated '{team_event.name}'. Please review the new details and check if it still fits your schedule.")
            Notification.objects.create(user=user, title=title, message=message, notification_type='team_event_updated', extra_data={'team_event_id': team_event.id,'attendance_id': attendance.id})
            notified_users.add(user.id)

        return redirect('event_list')

    context = {"event": team_event, "teams": user_teams }

    return render(request, "core/edit_team_event.html", context)


#CONFLICT CHECK FOR PERSONAL EVENTS AND TEAM EVENTS 
def has_conflict(new_event, kids=None, team=None):
    """
    Smart conflict detection that fully supports multiple kids.

    - kids: single Kid, list of Kids, or queryset
    - Properly excludes the event itself during edits
    - Checks both personal events and accepted team event attendances
    """
    if not new_event.start_time or not new_event.end_time:
        return False

    # Normalize kids to a list
    family_list = []
    if kids:
        if hasattr(kids, '__iter__') and not isinstance(kids, (str, bytes)):
            family_list = list(kids)
        else:
            family_list = [kids]

    if family_list:
        kid_ids = [k.id for k in family_list]

        # === Check personal/family events (M2M) ===
        personal_events = Event.objects.filter(
            created_by=new_event.created_by if hasattr(new_event, 'created_by') else None,
            kids__id__in=kid_ids,
        )
        if new_event.pk:
            personal_events = personal_events.exclude(pk=new_event.pk)

        for event in personal_events:
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
                return True

        # === Check accepted Team Events for any of these kids ===
        team_events = TeamEvent.objects.filter(
            attendances__kid__id__in=kid_ids,
            attendances__status='accepted'
        )
        for event in team_events:
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
                return True

    # === OWNER CREATING TEAM EVENT ===
    # (unchanged)
    if team:
        existing_team_events = TeamEvent.objects.filter(team=team)
        for event in existing_team_events:
            if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
                if getattr(new_event, 'pk', None) != event.pk:
                    return True

    return False


def get_kid_conflicts(kid, proposed_event):
    """
    Returns a list of events that conflict with the proposed_event for a single kid.
    Used during parent response to team events.
    Checks family events + accepted attendances on other team events + pending invitations.
    Overlap check is performed in Python for reliability (avoids any potential
    ORM datetime comparison / TZ edge cases with aware datetimes).
    """
    if not proposed_event.start_time or not proposed_event.end_time:
        return []

    conflicts = []

    # 1. Check Family/Personal Events for this kid.
    family_events = Event.objects.filter(
        kids=kid,
    )

    for event in family_events:
        overlaps = (event.start_time < proposed_event.end_time) and (event.end_time > proposed_event.start_time)
        if overlaps:
            conflicts.append({
                'type': 'family',
                'event': event,
                'reason': 'Family event conflict'
            })

    # 2. Check Accepted Team Events (other than this one)
    team_attendances = TeamEventAttendance.objects.filter(
        kid=kid,
        status='accepted',
    ).select_related('team_event').exclude(team_event=proposed_event)

    for attendance in team_attendances:
        ev = attendance.team_event
        overlaps = (ev.start_time < proposed_event.end_time) and (ev.end_time > proposed_event.start_time)
        if overlaps:
            conflicts.append({
                'type': 'team',
                'event': ev,
                'reason': 'Already attending another team event'
            })

    # 3. Also check other *pending* TeamEventInvitations for this parent, where the kid is registered
    # on that other team. This catches team-vs-team conflicts even before the other invitation has
    # been accepted (so both don't get auto-accepted leading to double-booked team events).
    # (Owner already prevents overlapping events on the *same* team.)
    try:
        pending_invs = TeamEventInvitation.objects.filter(
            user=kid.parent,
            status='pending'
        ).exclude(team_event=proposed_event).select_related('team_event__team')
        for inv in pending_invs:
            other_te = inv.team_event
            # Is this kid registered on the other team?
            registered = PlayerRegistration.objects.filter(
                kid=kid,
                team_membership__team=other_te.team
            ).exists()
            if registered:
                overlaps = (other_te.start_time < proposed_event.end_time) and (other_te.end_time > proposed_event.start_time)
                if overlaps:
                    conflicts.append({
                        'type': 'team',
                        'event': other_te,
                        'reason': 'Pending team event invitation for another team this kid is registered on'
                    })
    except Exception:
        pass

    return conflicts


def get_conflicts_for_kids(selected_kids, proposed_event):
    """
    Returns a dictionary of kids who have conflicts.
    Format: {kid: [list of conflict dicts]}
    Keys are the Kid model instances (supports 'kid in conflicts' and conflicts[kid]).

    This is a thin wrapper around get_kid_conflicts (called per kid).
    No other checks were altered.
    """
    conflicts = {}
    for kid in selected_kids:
        kid_conflicts = get_kid_conflicts(kid, proposed_event)
        if kid_conflicts:
            conflicts[kid] = kid_conflicts
    return conflicts


@login_required
def review_team_event_update(request, event_id):
    # Mark the clicked notification as read (if this link was followed from notifications)
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response

    try:
        team_event = TeamEvent.objects.get(id=event_id)
    
    except TeamEvent.DoesNotExist:
        messages.error(request, "This event no longer exists.")
        return redirect('event_list')
    

    attendances = list(TeamEventAttendance.objects.filter(
                team_event=team_event,
                kid__family=request.user.profile.family,
                status='accepted'
            ).select_related('kid'))

    if not attendances:
        messages.error(request, "You are not attending this event.")
        return redirect('event_list')
    
    # Check if this parent still needs to review the update for any kid
    if not any(attendance.needs_review for attendance in attendances):
        return redirect('event_list')

    # Get conflicts (for any of the parent's kids)
    kid_ids = [att.kid.id for att in attendances]
    conflicting_events = Event.objects.filter(
        created_by=request.user,
        kids__id__in=kid_ids,
        start_time__lt=team_event.end_time,
        end_time__gt=team_event.start_time,)


    context = {
        'event': team_event,
        'conflicting_events': conflicting_events,
        'chosen_kid': kid_ids,}
    
    return render(request, 'core/review_team_event_update.html', context)




#WHEN PARENTS DECIDE TO KEEP OR DELETE THE TEAM AFTER AN UPDATE TO AN EVENT 
@login_required
def keep_team_event_update(request, event_id):
    """Parent chooses to keep the updated team event"""
    # Mark specific notif if link had ?read (in addition to mass mark below)
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response

    try:
        event = TeamEvent.objects.get(id=event_id)
        
        # Mark the notification as read (optional but recommended)
        Notification.objects.filter(
            user=request.user,
            notification_type='team_event_updated'
        ).update(is_read=True)

        # Clear the needs_review flag now that the parent has handled the update notification
        TeamEventAttendance.objects.filter(
            team_event=event,
            kid__family=request.user.profile.family,
            status='accepted'
        ).update(needs_review=False)
        
        messages.success(request, f"You kept '{event.name}'. It will remain on your calendar with the updated details.")
        
    except TeamEvent.DoesNotExist:
        messages.error(request, "This event no longer exists.")
    
    return redirect('event_list')


@login_required
def remove_team_event_attendance(request, event_id):
    """Parent chooses to remove the team event from their calendar"""
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response

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
        if event.family and not (request.user.profile.family and request.user.profile.family.id == event.family.id):
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

            # Notify unique parents only (one notification per family/parent, even if multiple kids)
            # This fixes the duplicate notification bug (e.g. 3 kids -> 3 identical notifs)
            attendances = TeamEventAttendance.objects.filter(
                team_event=team_event,
                status='accepted'
            ).select_related('kid__parent')

            # Group by parent to send one notif with affected kids info
            parent_to_kids = defaultdict(list)
            for att in attendances:
                parent_to_kids[att.kid.parent].append(att.kid.first_name)

            for parent, kid_names in parent_to_kids.items():
                kids_str = ", ".join(kid_names)
                message = f"The event '{event_name}' for {team_name} ({org_name}) has been canceled by the organizer."
                if kids_str:
                    message += f" This affects: {kids_str}."
                Notification.objects.create(
                    user=parent,
                    title="Team Event Canceled",
                    message=message,
                    notification_type='team_event_canceled',
                    extra_data={'team_event_id': team_event.id}
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
def event_list(request):
    # Mark specific notif as read if clicked from notifications (e.g. for canceled team events)
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response

    if request.user.profile.role == "parent":
        user_family = request.user.profile.family
        user_families = [user_family] if user_family else []

        # Personal Events
        personal_events = list(Event.objects.filter(
            family__in=user_families,
        ).prefetch_related('kids', 'family', 'created_by'))

        # Accepted Team Events (family-wide for all kids in the family)
        accepted_team_events = list(TeamEvent.objects.filter(
            attendances__kid__family=user_family,
            attendances__status='accepted'
        ).select_related('team').distinct())

        # Combine both
        all_events = personal_events + accepted_team_events
        all_events.sort(key=lambda x: x.start_time)

        # Attach attendances for team events (for delete button) - supports multiple kids
        for event in all_events:
            if hasattr(event, 'attendances'):  # This is a TeamEvent
                user_attendances = list(event.attendances.filter(
                    kid__family=user_family,
                    status='accepted'
                ).select_related('kid'))
                if user_attendances:
                    event.user_attendances = user_attendances

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
                return redirect('owner_onboarding')
            else:
                return redirect('create_first_family')
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
            return redirect('account_settings')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "core/change_password.html", {'form': form})


@login_required
def add_kid(request):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    if request.method == "POST":
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        date_of_birth = request.POST.get("date_of_birth")
        gender = request.POST.get("gender")
        user_family = request.user.profile.family

        #CHECK IF USER IS PART OF A FAMILY
        if not user_family:
            messages.error(request, "You must be part of a family before adding kids.")
            return redirect('setup_family')
        #CHECK IF USER IS PART OF THE FAMILY THE KID IS BEING ADDED TO
        if user_family != request.user.profile.family:
            messages.error(request, "You do not have permission to add kids to this family.")
            return redirect('family_list')
            
   
        #creating the kid and saving it to the parent and database
        kid = Kid(first_name=first_name, last_name=last_name, date_of_birth=date_of_birth, 
        gender=gender)
        kid.parent = request.user
        kid.family = user_family
        kid.save()
        return redirect('family_list')

    #GET request
    return render(request, "core/add_kid.html")



@login_required
def edit_kid(request, kid_id):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    kid = Kid.objects.get(id= kid_id)

    if not (kid.family and request.user.profile.family and request.user.profile.family == kid.family):
        messages.error(request, "You do not have permission to edit this kid.")
        return redirect('family_list')

    if request.method == "POST":
        kid.first_name = request.POST.get("first_name")
        kid.last_name = request.POST.get("last_name")
        kid.date_of_birth = request.POST.get("date_of_birth")
        kid.gender = request.POST.get("gender")
        kid.save()
        messages.success(request, "Kid information updated successfully.")
        return redirect('family_list')
    
    return render(request, "core/edit_kid.html", {"kid": kid})

@login_required
def delete_kid(request, kid_id):
    try:
        kid = Kid.objects.get(id=kid_id)
    except Kid.DoesNotExist:
        messages.error(request, "This kid does not exist.")
        return redirect('kid_list')

    # Authorization checks
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    if not kid.family or request.user.profile.family != kid.family:
        messages.error(request, "You are not authorized to delete this kid.")
        return redirect('dashboard')

    kid_name = f"{kid.first_name} {kid.last_name}"

    if request.method == "POST":
        # 1. Handle Family Events
        for event in kid.events.all():
            if event.kids.count() == 1:
                event.delete()                    # Delete entire event if last kid
            else:
                event.kids.remove(kid)            # Remove kid from multi-kid event

        # 2. Remove from Team Registrations
        PlayerRegistration.objects.filter(kid=kid).delete()

        # 3. Delete the kid
        kid.delete()

        messages.success(request, f"{kid_name} has been deleted successfully.")
        return redirect('family_list')            # or 'kid_list'

    # GET request - show confirmation page
    context = {
        'kid': kid,
        'kid_name': kid_name
    }
    return render(request, "core/delete_kid.html", context)



@login_required
def kid_list(request):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    user_family = request.user.profile.family
    if user_family != request.user.profile.family and request.user.profile.role == "parent":
        redirect('dashboard')
    
    if user_family != request.user.profile.family and request.user.profile.role == "owner":
        redirect('owner_dashboard')
        
    if user_family:
        kids = Kid.objects.filter(family=user_family)
    else:
        kids = Kid.objects.none()
    return render(request,"core/kid_list.html",context= {"kids": kids})


#From here down is family views
@login_required
def add_family(request):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    if request.method == "POST":
        family_name = request.POST.get("family_name")
        new_family = Family(family_name=family_name, created_by=request.user)
        new_family.save()
        request.user.profile.family = new_family
        request.user.profile.save()
        return redirect('dashboard')
    
    return render(request, "core/add_family.html")


#Family List
@login_required
def family_list(request):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    user_family = request.user.profile.family
    families = [user_family] if user_family else []
    return render(request, "core/family_list.html", context={"families": families})


@login_required
def my_family(request):
    """Directly shows the current user's single family details."""
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    family = request.user.profile.family
    if not family:
        messages.error(request, "You don't have a family yet.")
        return redirect('create_first_family')

    # Reuse the same logic as family_detail for pending invites etc.
    pending_invites = Invite.objects.filter(
        family=family,
        status="pending"
    ).select_related('sender', 'receiver').order_by('-created_at')

    context = {
        "family": family,
        "pending_invites": pending_invites,
    }
    return render(request, "core/family_details.html", context)


@login_required
def parent_teams(request):
    """Page for parents to see all teams they belong to."""
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    memberships = TeamMembership.objects.filter(
        user=request.user,
        role='parent'
    ).select_related('team', 'team__organization')

    teams = [m.team for m in memberships]

    context = {
        "teams": teams,
    }
    return render(request, "core/parent_teams.html", context)


#Create first Family
@login_required
def create_first_family(request):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    # If user already has a family, redirect to dashboard
    if request.user.profile.family:
        return redirect('dashboard')
    
    if request.method == "POST":
        family_name = request.POST.get("family_name")
        new_family = Family(family_name=family_name, created_by=request.user)
        new_family.save()
        request.user.profile.family = new_family
        request.user.profile.save()
        return redirect('dashboard')
    
    return render(request, "core/create_first_family.html")

#SETUP FAMILY
@login_required
def setup_family(request):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    return render(request, "core/setup_family.html")


@login_required
def parent_onboarding(request):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')
    return render(request, "core/parent_onboarding.html")


@login_required
def owner_onboarding(request):
    if request.user.profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')
    return render(request, "core/owner_onboarding.html")


#ALL THINGS INVITE RELATED. FOR PARENT TO PARENT INVITE/REQUEST AS WELL AS TEAM TO PARENT AND PARENT TO TEAM INVITE/REQUEST


#JOIN FAMILY LOGIC FOR SIGNUP FLOW
@login_required
def join_family(request):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    if request.method == "POST":
        username = request.POST.get("username")

        try:
            target_user = User.objects.get(username=username)
        
        except User.DoesNotExist:
            messages.error(request, "This user does not exist")
            return render(request, "core/join_family.html")

        user_target_family = target_user.profile.family
        if not user_target_family:
            messages.error(request, f"This user does not have a family")
            return redirect('join_family')

        elif user_target_family:
            family = user_target_family
            if request.user.profile.family and target_user.profile.family == family:
                messages.error(request, f"You are already a member of the {family.family_name} family")
                return redirect('join_family')
                
            else:
                invite = Invite.objects.create(sender=request.user, receiver=target_user, family=family, status="pending", invite_type="join_request")
                messages.success(request, "Your family request was sent successfully")

                # Guard against duplicate notifications for the same invite (consistent with other invite flows)
                if not Notification.objects.filter(
                    user=target_user,
                    notification_type='family_join_request',
                    extra_data__invite_id=invite.id
                ).exists():
                    title = f"Join Family Request - {request.user}"
                    message = f"{request.user.get_full_name() or request.user.username} would like to join your family"
                    Notification.objects.create(
                        user=target_user,
                        title=title,
                        message=message,
                        notification_type='family_join_request',
                        extra_data={
                            'family_id': family.id,
                            'invite_id': invite.id,
                            'sender_id': request.user.id,
                        }
                    )
                
                return redirect('dashboard')
    
            
    return render(request, "core/join_family.html")


@login_required
@login_required
def accept_family_invite(request, invite_id):
    try:
        invite = Invite.objects.get(
            id=invite_id,
            receiver=request.user,
            status='pending'
        )
    except Invite.DoesNotExist:
        messages.error(request, "This invite does not exist or has expired.")
        return redirect('notifications')

    if not invite.family:
        messages.error(request, "This invitation is not associated with a family.")
        return redirect('notifications')

    # Defensive checks for already members
    receiver_in_family = invite.receiver.profile.family
    sender_in_family = invite.sender.profile.family

    if invite.invite_type == "sent_invite" and receiver_in_family:
        invite.delete()
        messages.info(request, "You were already a member of this family.")
        return redirect('family_detail', family_id=invite.family.id)

    if invite.invite_type == "family_join_request" and sender_in_family:
        invite.delete()
        messages.info(request, "This parent is already a member of the family.")
        return redirect('family_detail', family_id=invite.family.id)

    # Handle family invite types
    if invite.invite_type in ("join_request", "family_join_request"):
        invite.sender.profile.family = invite.family
        invite.sender.profile.save()

    elif invite.invite_type == "sent_invite":
        invite.receiver.profile.family = invite.family
        invite.receiver.profile.save()

    else:
        messages.error(request, "Invalid invite type for family acceptance.")
        return redirect('notifications')

    # Mark as accepted and notify
    invite.status = "accepted"
    invite.save()
    messages.success(request, "Invite accepted successfully!")

    if invite.invite_type in ("sent_invite", "join_request", "family_join_request"):
        if invite.invite_type in ("join_request", "family_join_request"):
            title = f"Join Request Accepted - {invite.family.family_name}"
            message = f"{request.user.get_full_name() or request.user.username} accepted your request to join {invite.family.family_name}."
        else:
            title = f"Family Invite Accepted - {invite.family.family_name}"
            message = f"{request.user.get_full_name() or request.user.username} accepted your invite to join {invite.family.family_name}."

        Notification.objects.create(
            user=invite.sender,
            title=title,
            message=message,
            notification_type='family_invite_accepted',
            extra_data={
                'family_id': invite.family.id,
                'invite_id': invite.id,
                'accepted_by_id': request.user.id,
            }
        )

    return redirect('family_detail', family_id=invite.family.id)

@login_required
def accept_team_invite(request, invite_id):
    try:
        invite = Invite.objects.get(
            id=invite_id,
            receiver=request.user,
            status='pending'
        )
    except Invite.DoesNotExist:
        messages.error(request, "This invite does not exist or has expired.")
        return redirect('notifications')

    if not invite.team:
        messages.error(request, "This invitation is not associated with a team.")
        return redirect('notifications')

    if invite.invite_type == "team_sent_invite":
        # Parent accepting a team owner's invitation
        invite.status = "accepted"
        invite.save()

        TeamMembership.objects.get_or_create(
            team=invite.team,
            user=invite.receiver,
            defaults={'role': 'parent'}
        )
        return redirect('select_kids_for_team_roster', invite_id=invite.id)

    elif invite.invite_type == "team_join_request":
        # Team owner reviewing a parent's join request.
        # Do NOT mark accepted yet — review_roster_request expects status='pending'
        return redirect('review_roster_request', invite_id=invite.id)

    else:
        messages.error(request, "Invalid invite type for team acceptance.")
        return redirect('notifications')

    



@login_required
def decline_invite(request, invite_id):
    invite = Invite.objects.get(id=invite_id)

    if invite.receiver != request.user:
        messages.error(request, "You are not authorized to decline this invite.")
        return redirect('dashboard')
    
    # Clean up roster request notifications when owner declines a parent-to-team request
    if invite.invite_type == "team_join_request":
        roster_notifs = Notification.objects.filter(
            user=request.user,
            notification_type='roster_request',
            is_read=False
        )
        for notif in roster_notifs:
            if notif.extra_data and notif.extra_data.get('invite_id') == invite.id:
                notif.is_read = True
                notif.save()

    # Notify the original sender for family invites before deleting
    if invite.family and invite.invite_type in ("sent_invite", "join_request", "family_join_request"):
        if invite.invite_type in ("join_request", "family_join_request"):
            title = f"Join Request Declined - {invite.family.family_name}"
            message = f"{request.user.get_full_name() or request.user.username} declined your request to join {invite.family.family_name}."
        else:
            title = f"Family Invite Declined - {invite.family.family_name}"
            message = f"{request.user.get_full_name() or request.user.username} declined your invite to join {invite.family.family_name}."

        Notification.objects.create(
            user=invite.sender,
            title=title,
            message=message,
            notification_type='family_invite_declined',
            extra_data={
                'family_id': invite.family.id,
                'invite_id': invite.id,
                'declined_by_id': request.user.id,
            }
        )

    invite.delete()
    messages.success(request, "Invite has been declined.")
    return redirect('notifications')



@login_required
def invite_parent(request, family_id):
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    try: 
        family = Family.objects.get(id=family_id)
    except Family.DoesNotExist:
        messages.error(request, "Family does not exist")
        return redirect('family_list')
    
    if request.method == "POST":
        username = request.POST.get("username")

        try:
            target_user = User.objects.get(username=username)
        except User.DoesNotExist:
            messages.error(request, "User does not exist")
            return redirect('invite_parent', family_id=family_id)
            
        if target_user == request.user:
                messages.error(request, "You are already a member of this family!")
                return redirect('invite_parent', family_id=family_id)

        elif target_user.profile.family and target_user.profile.family == family:
                messages.error(request, f"{target_user.username} is already a member of the family")
                return redirect('invite_parent', family_id=family_id)

        elif Invite.objects.filter(family=family, receiver=target_user, status="pending"):
                messages.error(request, "An invite has already been sent")
                return redirect('invite_parent', family_id=family_id)

        else:
            invite = Invite.objects.create(sender=request.user, receiver=target_user, family=family, status="pending", invite_type="sent_invite")

            # Send notification only to the recipient (the invited parent). Sender receives nothing.
            # Guard against duplicate notification records for the same invite.
            if not Notification.objects.filter(
                user=target_user,
                notification_type='parent_invite',
                extra_data__invite_id=invite.id
            ).exists():
                title = f"Parent Invite - {family.created_by}"
                message = f"{request.user.username} has invited you to join {family.family_name}."
                Notification.objects.create(
                    user=target_user,
                    title=title,
                    message=message,
                    notification_type='parent_invite',
                    extra_data={
                        'family_id': family.id,
                        'invite_id': invite.id,
                        'sender_id': request.user.id
                    }
                )

            messages.success(request, "Invite sent successfully")
            return redirect('dashboard')
    
    

        

    
    return render(request, "core/invite_parent.html")

@login_required
def team_invite_to_parent(request, team_id, username):
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        messages.error(request, "Team does not exist.")
        return redirect('team_list')

    try:
        target_user = User.objects.get(username__iexact=username)
    except User.DoesNotExist:
        messages.error(request, "User does not exist.")
        return redirect('team_to_parent_invite_search', team_id=team_id)

    # PERMISSION CHECK
    if request.user.profile.role != "owner":
        messages.error(request, "You are not authorized to send this invite.")
        return redirect('dashboard')

    # Prevent self-invite
    if team.organization.owner_id == target_user.id:
        messages.error(request, "You cannot invite yourself.")
        return redirect('team_to_parent_invite_search', team_id=team_id)

    # Block if there's already a PENDING invite
    if Invite.objects.filter(
        team=team, 
        receiver=target_user, 
        status="pending"
    ).exists():
        messages.error(request, "This user already has a pending invite for this team.")
        return redirect('team_to_parent_invite_search', team_id=team_id)

    # NEW CHECK: Only allow invite if parent has unregistered kids left
    eligible_kids = get_unregistered_kids_for_team(target_user, team)
    
    if not eligible_kids.exists():
        messages.error(request, f"{target_user.username} already has all their kids registered on this team.")
        return redirect('team_to_parent_invite_search', team_id=team_id)

    # CREATE THE INVITE
    invite = Invite.objects.create(
        team=team,
        sender=request.user,
        receiver=target_user,
        invite_type="team_sent_invite",
        status="pending"
    )

    # Notify the invited parent
    if not Notification.objects.filter(
        user=target_user,
        notification_type='team_invite',
        extra_data__invite_id=invite.id
    ).exists():
        title = f"Roster Invite - {team.name}"
        message = f"{team.name} has invited you to join {team.name}."
        Notification.objects.create(
            user=target_user,
            title=title,
            message=message,
            notification_type='team_invite',
            extra_data={
                'team_id': team.id,
                'invite_id': invite.id,
                'sender_id': team.organization.owner.id
            }
        )

    messages.success(request, f"Invite sent to {target_user.username} successfully!")
    return redirect('owner_dashboard')


def get_unregistered_kids_for_team(parent_user, team):
    """Return the kids in the parent's family that are not yet registered on this specific team."""
    if not parent_user or not team:
        return Kid.objects.none()

    user_family = parent_user.profile.family
    if not user_family:
        return Kid.objects.none()

    registered_kid_ids = PlayerRegistration.objects.filter(
        team_membership__team=team,
        kid__family=user_family
    ).values_list('kid_id', flat=True)

    return Kid.objects.filter(
        family=user_family
    ).exclude(id__in=registered_kid_ids).order_by('first_name', 'last_name')


@login_required
def select_kids_for_team_join_request(request, team_id):
    """Kid selection step before sending a parent-to-team roster request.
    Now supports parents adding additional kids even if they have a pending or accepted request.
    """
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        messages.error(request, "Team does not exist.")
        return redirect('find_teams')

    if request.user.profile.role != "parent":
        messages.error(request, "Only parents can send roster requests.")
        return redirect('owner_dashboard')

    # Prevent owners from sending roster requests to their own teams
    if team.organization.owner_id == request.user.id:
        messages.error(request, "You own this team. You cannot send a roster request to yourself.")
        return redirect('owner_dashboard')

    # Get kids who are NOT yet registered on this team
    eligible_kids = get_unregistered_kids_for_team(request.user, team)

    # Only block if there are NO eligible kids left
    if not eligible_kids.exists():
        messages.info(request, "All of your kids are already on this team.")
        return redirect('find_teams')

    # ←←← REMOVED the strict pending request check
    # Parents can now send additional requests for remaining kids

    context = {
        "team": team,
        "kids": eligible_kids,
    }
    return render(request, "core/select_kids_for_team_join_request.html", context)


@login_required
def parent_to_team_request(request, team_id):
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        messages.error(request, "Team does not exist.")
        return redirect('find_teams')

    if request.user.profile.role != "parent":
        messages.error(request, "You are not authorized to send this request")
        return redirect('owner_dashboard')

    # Prevent owners from sending roster requests to their own teams
    if team.organization.owner_id == request.user.id:
        messages.error(request, "You own this team. You cannot send a roster request to yourself.")
        return redirect('owner_dashboard')

    if request.method == "POST":
        # Support new flow: kid selection submits here with selected kids
        selected_kid_ids = request.POST.getlist('kids')

        # Allow additional kids even if parent already has a membership on the team
        eligible_kids = get_unregistered_kids_for_team(request.user, team)
        if not eligible_kids.exists():
            messages.error(request, 'All of your kids are already registered on this team.')
            return redirect('find_teams')


        if not selected_kid_ids:
            messages.error(request, "Please select at least one kid for the roster request.")
            return redirect('select_kids_for_team_join_request', team_id=team.id)

        # Create the join request
        invite = Invite.objects.create(
            team=team,
            sender=request.user,
            receiver=team.organization.owner,
            invite_type="team_join_request",
            status="pending",
            extra_data={}  # keep empty
        )

        # Store the requested kids
        RosterRequestKid.objects.bulk_create([
            RosterRequestKid(invite=invite, kid_id=int(kid_id))
            for kid_id in selected_kid_ids
        ])

        messages.success(request, 'Roster request sent. The team owner will review your request.')

        # Notify the owner
        if not Notification.objects.filter(
            user=team.organization.owner,
            notification_type='roster_request',
            extra_data__invite_id=invite.id
        ).exists():
            title = f"Roster Request - {team.name}"
            message = f"{request.user.get_full_name() or request.user.username} has requested to join {team.name}."
            Notification.objects.create(
                user=team.organization.owner,
                title=title,
                message=message,
                notification_type='roster_request',
                extra_data={
                    'team_id': team.id,
                    'invite_id': invite.id,
                    'sender_id': request.user.id,
                }
            )

        return redirect('find_teams')

    # GET requests: force the new kid selection flow for parents
    if request.user.profile.role == "parent":
        return redirect('select_kids_for_team_join_request', team_id=team.id)

    # Fallback for non-parents
    return render(request, "core/parent_to_team_request.html", {"team": team})


def team_invite_response(request, invite_id):
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response
    try:
        invite = Invite.objects.get(id=invite_id,receiver=request.user,status='pending')

    except Invite.DoesNotExist:
        messages.error(request, "This invitation is no longer valid or has expired.")
        return redirect('notifications')
    except Exception as e:
        messages.error(request, f"Error: {str(e)}")
        return redirect('notifications')

    team = invite.team

    if not team:
        messages.error(request, "This invitation is not linked to a team.")
        return redirect('notifications')

    family = request.user.profile.family

    if not family:
        messages.error(request, "No family profile found.")
        return redirect('home')

    total_kids = family.kids.count()

    kids_on_team = PlayerRegistration.objects.filter(
        team_membership__team=team,
        kid__in=family.kids.all()
    ).count()

    remaining_kids = total_kids - kids_on_team

    if remaining_kids <= 0:
        messages.info(request, "All your kids are already registered on this team.")
        return redirect('team_detail', team_id=team.id)

    context = {
        'invite': invite,
        'team': team,
        'family': family,
        'total_kids': total_kids,
        'kids_on_team': kids_on_team,
        'remaining_kids': remaining_kids,
    }

    return render(request, 'core/team_invite_response.html', context)


@login_required
def family_invite_response(request, invite_id):
    """Dedicated response page for family membership actions.
    Handles both directions:
    - sent_invite: the invited parent reviews an invitation they received.
    - join_request: the family owner reviews a request from another parent to join their family.
    Shows clear Accept / Decline actions.
    """
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response
    try:
        invite = Invite.objects.get(
            id=invite_id,
            receiver=request.user,
            status="pending",
            invite_type__in=["sent_invite", "join_request", "family_join_request"]
        )
    except Invite.DoesNotExist:
        messages.error(request, "This family invitation is no longer valid or has already been handled.")
        return redirect('notifications')

    family = invite.family
    if not family:
        # Family was deleted
        invite.delete()
        messages.error(request, "This family no longer exists.")
        return redirect('notifications')

    # Defensive check: only relevant when the current user is the one being invited (sent_invite direction).
    # For join requests, the viewer is the family owner, who is expected to already be in the family.
    if invite.invite_type in ("sent_invite",) and request.user.profile.family and request.user.profile.family == family:
        invite.delete()  # Clean up the useless pending invite
        messages.info(request, "You are already a member of this family.")
        return redirect('family_detail', family_id=family.id)

    is_join_request = invite.invite_type in ("join_request", "family_join_request")

    context = {
        "invite": invite,
        "family": family,
        "sender": invite.sender,
        "is_join_request": is_join_request,
    }
    return render(request, "core/family_invite_response.html", context)


# Removed: team_approve_join_request was a stub and no longer needed.


@login_required
def review_roster_request(request, invite_id):
    """Owner reviews a parent's roster join request (team_join_request).
    Shows the requested kids (pre-selected by parent) and allows owner to approve
    a subset or all of them, or decline via existing decline_invite.
    Template name matches the view per requirement.
    """
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response
    try:
        invite = Invite.objects.get(
            id=invite_id,
            receiver=request.user,
            status="pending",
            invite_type="team_join_request"
        )
    except Invite.DoesNotExist:
        messages.error(request, "This roster request is no longer valid or has already been handled.")
        return redirect('notifications')

    team = invite.team
    if not team:
        messages.error(request, "This request is not associated with a team.")
        return redirect('notifications')

    # Only the team owner (the invite receiver) is allowed to review.
    # Block the (impossible in normal flow) case of a user reviewing a request they themselves sent.
    if invite.sender == request.user:
        messages.error(request, "You cannot review a roster request that you sent.")
        return redirect('notifications')

    # Verify the current user is still the owner of this team/organization
    if not team.organization or team.organization.owner != request.user:
        messages.error(request, "You are not authorized to review this roster request.")
        return redirect('notifications')

    # Mark any matching roster_request notifications as read when the owner opens the review
    roster_notifs = Notification.objects.filter(
        user=request.user,
        notification_type='roster_request',
        is_read=False
    )
    for notif in roster_notifs:
        if notif.extra_data and notif.extra_data.get('invite_id') == invite.id:
            notif.is_read = True
            notif.save()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve":
            selected_kid_ids = request.POST.getlist("kids")
            if not selected_kid_ids:
                messages.error(request, "Please select at least one kid to approve for the roster.")
                return redirect('review_roster_request', invite_id=invite.id)

            # Ensure parent has membership on the team
            membership, _ = TeamMembership.objects.get_or_create(
                team=team,
                user=invite.sender,
                defaults={'role': 'parent'}
            )

            # Kid IDs that were requested for this invite (via RosterRequestKid)
            allowed_kid_ids = set(invite.requested_kids.values_list('kid_id', flat=True))

            added_count = 0
            for kid_id in selected_kid_ids:
                try:
                    kid_id_int = int(kid_id)
                    if kid_id_int not in allowed_kid_ids:
                        continue  # Ignore kids not part of the original request

                    kid = Kid.objects.get(id=kid_id_int, parent=invite.sender)
                    if not PlayerRegistration.objects.filter(team_membership=membership, kid=kid).exists():
                        PlayerRegistration.objects.create(team_membership=membership, kid=kid)
                        added_count += 1
                except (Kid.DoesNotExist, ValueError):
                    continue

            invite.status = "accepted"
            invite.save()

            messages.success(
                request,
                f"Roster request approved. {added_count} kid(s) added to {team.name}."
            )
            return redirect('owner_dashboard')

    # GET: prepare requested kids for display/selection
    requested_kids = [
        rk.kid for rk in invite.requested_kids.select_related('kid').all()
    ]

    context = {
        "invite": invite,
        "team": team,
        "requester": invite.sender,
        "requested_kids": requested_kids,
        "organization": team.organization if team else None,
    }
    return render(request, "core/review_roster_request.html", context)





@login_required
def remove_parent(request, family_id, parent_id,):
    parent = User.objects.get(id=parent_id)
    family = Family.objects.get(id=family_id)
    if parent == family.created_by and request.user != family.created_by:
        messages.error(request, "You can not delete the creator of the family.")
        return redirect('family_list')
    if request.method == "POST":
        if parent.profile.family == family:
            parent.profile.family = None
            parent.profile.save()
        if parent == family.created_by:
            family.delete()
        return redirect('family_list')

    return render(request, "core/remove_parent.html", context={"parent": parent})

@login_required
def family_detail(request, family_id):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    family = Family.objects.get(id=family_id)

    # Permission check
    if not request.user.profile.family or request.user.profile.family.id != family.id:
        messages.error(request, "You do not have access to this family.")
        return redirect('family_list')

    # Pending family invites (both sent by this family and requests to join this family)
    pending_invites = Invite.objects.filter(
        family=family,
        status="pending"
    ).select_related('sender', 'receiver').order_by('-created_at')

    context = {
        "family": family,
        "pending_invites": pending_invites,
    }
    return render(request, "core/family_details.html", context)


@login_required
def event_detail(request, event_id):
    """Detail page for a personal/family event."""
    if request.user.profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        messages.error(request, "Event not found.")
        return redirect('event_list')

    # Permission: must be in the same family
    if not event.family or not request.user.profile.family or request.user.profile.family.id != event.family.id:
        messages.error(request, "You do not have access to this event.")
        return redirect('event_list')

    context = {
        "event": event,
        "is_personal": True,
    }
    return render(request, "core/event_detail.html", context)


@login_required
def team_event_detail(request, event_id):
    """Detail page for a team event."""
    try:
        team_event = TeamEvent.objects.get(id=event_id)
    except TeamEvent.DoesNotExist:
        messages.error(request, "Team event not found.")
        return redirect('event_list')

    # Permission: owner of the team OR has an accepted attendance for one of their family kids
    user_family = request.user.profile.family
    has_access = False

    if team_event.team.organization.owner == request.user:
        has_access = True
    elif user_family:
        has_attendance = TeamEventAttendance.objects.filter(
            team_event=team_event,
            kid__family=user_family,
            status='accepted'
        ).exists()
        if has_attendance:
            has_access = True

    if not has_access:
        messages.error(request, "You do not have access to this team event.")
        return redirect('event_list')

    # Get attendances for display
    attendances = team_event.attendances.select_related('kid').all()

    context = {
        "team_event": team_event,
        "attendances": attendances,
        "is_team_event": True,
    }
    return render(request, "core/team_event_detail.html", context)


@login_required
def team_detail(request, team_id):
    """Detail page for a team."""
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        messages.error(request, "Team not found.")
        return redirect('team_list')

    # Permission: owner of the org OR any member of this specific team
    is_owner = (team.organization.owner == request.user)
    is_team_member = TeamMembership.objects.filter(team=team, user=request.user).exists()

    if not (is_owner or is_team_member):
        messages.error(request, "You do not have access to this team.")
        return redirect('team_list')

    # Get roster / players
    players = PlayerRegistration.objects.filter(
        team_membership__team=team
    ).select_related('kid', 'team_membership__user')

    context = {
        "team": team,
        "players": players,
    }
    return render(request, "core/team_detail.html", context)


def _mark_notification_as_read(request):
    """Mark a specific notification as read if the 'read' query param is present.
    Used when following a notification link to ensure the clicked notif is marked read,
    fixing inconsistent greying.
    If a 'read' param was present, returns a redirect to the clean path (no ?read in URL).
    """
    notif_id = request.GET.get('read')
    if notif_id:
        try:
            Notification.objects.filter(
                id=int(notif_id), user=request.user
            ).update(is_read=True)
            # Redirect cleanly without the ?read param in the browser URL
            return redirect(request.path)
        except (ValueError, TypeError):
            pass
    return None


@login_required
def notifications(request):
    """
    Notifications page - marks notifications as read when clicked.
    """
    user = request.user

    # Mark individual notification as read when clicked (via ?read= param on this page or targets)
    # If ?read was present, this will return a redirect response to clean URL
    response = _mark_notification_as_read(request)
    if response:
        return response

    notifications_qs = Notification.objects.filter(user=user).order_by('-created_at')
    items = []

    for n in notifications_qs:
        extra = n.extra_data or {}
        ntype = n.notification_type

        # Determine base URL WITHOUT any query parameters
        if ntype == "team_event_updated":
            team_event_id = extra.get("team_event_id") or extra.get("event_id")
            base_url = reverse('review_team_event_update', args=[team_event_id]) if team_event_id else reverse('notifications')
        elif ntype == "team_event_invitation":
            invitation_id = extra.get("invitation_id") or extra.get("team_event_id")
            base_url = reverse('team_event_kid_selection', args=[invitation_id]) if invitation_id else reverse('notifications')
        elif ntype == "roster_request":
            invite_id = extra.get("invite_id")
            base_url = reverse('review_roster_request', args=[invite_id]) if invite_id else reverse('notifications')
        elif ntype == "team_invite":
            invite_id = extra.get("invite_id")
            base_url = reverse('team_invite_response', args=[invite_id]) if invite_id else reverse('notifications')
        elif ntype in ("family_invite", "parent_invite", "family_join_request"):
            invite_id = extra.get("invite_id")
            base_url = reverse('family_invite_response', args=[invite_id]) if invite_id else reverse('notifications')
        elif ntype == "team_event_canceled":
            base_url = reverse('event_list')
        else:
            base_url = reverse('notifications')

        # Properly append the read parameter
        if '?' in base_url:
            final_url = f"{base_url}&read={n.id}"
        else:
            final_url = f"{base_url}?read={n.id}"

        items.append({
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "created_at": n.created_at,
            "is_read": n.is_read,
            "type": ntype,
            "url": final_url,
        })

    unread_count = sum(1 for i in items if not i["is_read"])

    # Mark all as read
    if request.method == "POST":
        Notification.objects.filter(user=user, is_read=False).update(is_read=True)
        messages.success(request, "All notifications marked as read.")
        return redirect('notifications')

    return render(request, "core/notifications.html", {
        "notifications": items,
        "unread_count": unread_count,
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
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

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

    # Annotate each team with membership + whether the parent can still add more kids
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

        # True if the parent has at least one kid not yet on this team
        team.can_add_more_kids = get_unregistered_kids_for_team(request.user, team).exists()

        # Important: mark if this is one of the current user's own teams
        team.is_owned_by_user = team.organization.owner_id == request.user.id

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

    # Only show kids that are not already on this team's roster
    eligible_kids = get_unregistered_kids_for_team(request.user, invite.team)

    if request.method == "POST":
        selected_kid_ids = request.POST.getlist('kids')

        if not selected_kid_ids:
            messages.error(request, "Please select at least one kid.")
        else:
            for kid_id in selected_kid_ids:
                try:
                    kid = Kid.objects.get(id=kid_id, family=request.user.profile.family)
                    # Extra safety: don't re-register
                    if not PlayerRegistration.objects.filter(team_membership=membership, kid=kid).exists():
                        PlayerRegistration.objects.create(
                            team_membership=membership, 
                            kid=kid
                        )
                except Kid.DoesNotExist:
                    continue

            # Mark the invite as accepted
            invite.status = "accepted"
            invite.save()

            messages.success(request, "Kids successfully added to the team!")
            return redirect('dashboard')

    # GET request - show the form
    context = {
        'invite': invite,
        'kids': eligible_kids,
        'team': invite.team,
    }
    return render(request, "core/select_kids_for_team_roster.html", context)

@login_required
def decline_team_event_invite(request, team_event_invitation_id, kid_id):
    """Skip a specific kid (create/update declined attendance).
    Used from the conflict resolution screen for kids that have conflicts.
    Keeps the original family event (does not replace).
    Prunes from pending, and on final kid finalizes invitation.status.
    """
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
        team_event = invitation.team_event
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    if invitation.user != request.user:
        messages.error(request, "Unauthorized.")
        return redirect('notifications')

    user_family = request.user.profile.family
    try:
        kid = Kid.objects.get(id=kid_id, family=user_family)
    except Kid.DoesNotExist:
        messages.error(request, "Kid not found.")
        return redirect('notifications')

    # Record the skip (declined). Use update_or_create so we can flip decisions if needed.
    TeamEventAttendance.objects.update_or_create(
        team_event=team_event,
        kid=kid,
        defaults={'status': 'declined'}
    )

    messages.info(request, f"{kid.first_name} was skipped for this team event.")

    # === Session / pending management ===
    pending_key = 'team_event_pending_kids'
    original_key = 'team_event_original_selection'
    legacy_key = 'selected_kid_ids'

    pending_list = request.session.get(pending_key) or request.session.get(legacy_key, [])
    if pending_list:
        remaining = [str(k) for k in pending_list if int(k) != int(kid_id)]
        if remaining:
            request.session[pending_key] = remaining
            return redirect('resolve_team_event_conflict', team_event_invitation_id=team_event_invitation_id)
        else:
            # Was the last pending kid
            request.session.pop(pending_key, None)
            request.session.pop(original_key, None)
            request.session.pop(legacy_key, None)

    # Finalize: accepted only if at least one kid (possibly auto-accepted clears or previous replaces) ended up accepted
    has_any_accepted = TeamEventAttendance.objects.filter(
        team_event=team_event,
        kid__family=user_family,
        status='accepted'
    ).exists()
    invitation.status = "accepted" if has_any_accepted else "declined"
    invitation.save()

    # If everything was skipped we still go to event_list (the team event simply won't appear for this family)
    return redirect('event_list')


@login_required
def team_event_kid_selection(request, team_event_invitation_id):
    """Parent selects which of their registered kids to send to a team event.
    Checks for conflicts on selected kids only.
    Non-conflicting kids are auto-accepted immediately.
    If any conflicts, stores original selection + pending conflict kids in session
    and redirects to per-kid resolution screen.
    """
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
        team_event = invitation.team_event
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    if invitation.user != request.user:
        messages.error(request, "Unauthorized.")
        return redirect('notifications')

    user_family = request.user.profile.family

    # Mark the clicked notification as read (if this link was followed from notifications)
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response

    # If invitation already fully resolved, don't allow re-selection
    if invitation.status in ('accepted', 'declined'):
        return redirect('event_list')

    # If we are mid-conflict-resolution (have pending), clicking notif link again should take you back to resolve
    if request.session.get('team_event_pending_kids'):
        return redirect('resolve_team_event_conflict', team_event_invitation_id=team_event_invitation_id)

    # Legacy / safety: if already has accepted attendances and no pending session, go to list
    if TeamEventAttendance.objects.filter(
        kid__parent=request.user,
        team_event=team_event,
        status="accepted"
    ).exists():
        return redirect('event_list')

    kids = Kid.objects.filter(
        playerregistration__team_membership__team=team_event.team,
        family=user_family
    ).distinct() if user_family else Kid.objects.none()

    if request.method == "POST":
        selected_kid_ids = request.POST.getlist('kids')

        if not selected_kid_ids:
            messages.error(request, "Please select at least one kid.")
            return render(request, "core/team_event_kid_selection.html", {
                'invitation': invitation,
                'team_event': team_event,
                'kids': kids
            })

        # Normalize to strings for session
        selected_kid_ids = [str(kid_id) for kid_id in selected_kid_ids]

        # Query the actual kid objects (security: scoped to family)
        selected_kids = list(Kid.objects.filter(id__in=selected_kid_ids, family=user_family))

        if not selected_kids:
            messages.error(request, "Invalid kid selection.")
            return render(request, "core/team_event_kid_selection.html", {
                'invitation': invitation,
                'team_event': team_event,
                'kids': kids
            })

        # Store original selection for the resolve screen (so we can show clears + conflicts)
        request.session['team_event_original_selection'] = selected_kid_ids

        # === Check conflicts and auto-accept non-conflicting kids ===
        conflicts = get_conflicts_for_kids(selected_kids, team_event)

        # Use id-based check for robustness (avoids any potential model instance eq/hash edge cases in dict 'in')
        conflicting_kid_ids = {k.id for k in conflicts.keys()}

        pending_kid_ids = []
        auto_accepted_count = 0

        for kid in selected_kids:
            if kid.id in conflicting_kid_ids:
                pending_kid_ids.append(str(kid.id))
            else:
                # Auto-accept clear kids right away so they are "already marked as going"
                TeamEventAttendance.objects.update_or_create(
                    team_event=team_event,
                    kid=kid,
                    defaults={'status': 'accepted'}
                )
                auto_accepted_count += 1

        if pending_kid_ids:
            # Some kids have conflicts → store only the pending ones for the wizard
            request.session['team_event_pending_kids'] = pending_kid_ids
            # Clean legacy key
            request.session.pop('selected_kid_ids', None)

            if auto_accepted_count > 0:
                messages.info(
                    request,
                    f"{auto_accepted_count} kid(s) with no conflicts were automatically added. "
                    "Please resolve the remaining kid(s) with conflicts."
                )
            return redirect('resolve_team_event_conflict', team_event_invitation_id=team_event_invitation_id)
        else:
            # No conflicts at all → everything already accepted above
            # Clean session state
            request.session.pop('team_event_original_selection', None)
            request.session.pop('team_event_pending_kids', None)
            request.session.pop('selected_kid_ids', None)

            # Set invitation status (at least one accepted, or none if user selected zero valid)
            has_accepted = TeamEventAttendance.objects.filter(
                team_event=team_event,
                kid__family=user_family,
                status='accepted'
            ).exists()
            invitation.status = "accepted" if has_accepted else "declined"
            invitation.save()

            if auto_accepted_count > 0:
                messages.success(request, f"All {auto_accepted_count} selected kid(s) added to the team event successfully!")
            else:
                messages.info(request, "No kids were added.")
            return redirect('event_list')

    # GET request - show selection form
    context = {
        'invitation': invitation,
        'team_event': team_event,
        'kids': kids,
    }
    return render(request, "core/team_event_kid_selection.html", context)

@login_required
def resolve_team_event_conflict(request, team_event_invitation_id):
    """Show conflict resolution screen.
    Displays *all* originally selected kids (clears that were auto-accepted + ones still needing decision).
    Only kids that do not yet have an attendance record for this team event will show Replace/Skip buttons.
    Uses 'team_event_original_selection' (full list) and 'team_event_pending_kids' (still to decide).
    """
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
        team_event = invitation.team_event
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    if invitation.user != request.user:
        messages.error(request, "You are not authorized.")
        return redirect('notifications')

    user_family = request.user.profile.family

    # Prefer the new keys; fall back to legacy 'selected_kid_ids' for the display list
    original_ids = request.session.get('team_event_original_selection') or request.session.get('selected_kid_ids', [])
    pending_ids = request.session.get('team_event_pending_kids', [])

    if not original_ids:
        messages.error(request, "No kids were selected for conflict resolution.")
        return redirect('notifications')

    # Clean legacy key if we migrated
    if 'selected_kid_ids' in request.session and 'team_event_original_selection' in request.session:
        request.session.pop('selected_kid_ids', None)

    selected_kids = Kid.objects.filter(id__in=original_ids, family=user_family)

    # Current conflicts (will only be non-empty for kids whose conflicting events have not yet been resolved)
    conflicts = get_conflicts_for_kids(selected_kids, team_event)

    # Map of kid_id -> current attendance status for this team event (accepted/declined)
    # Used by template to hide buttons for already-decided kids and show final status
    att_status_by_kid = {}
    for att in TeamEventAttendance.objects.filter(
        team_event=team_event,
        kid__in=selected_kids
    ).select_related('kid'):
        att_status_by_kid[att.kid_id] = att.status

    # Use id sets for robustness
    conflicting_ids = {k.id for k in conflicts.keys()}

    # Prepare a list for the template so we have easy per-kid conflict lists (avoids the old conflicts.kid template bug)
    # and can render decided vs undecided cleanly.
    kids_data = []
    for kid in selected_kids:
        kid_conflicts = conflicts.get(kid, [])
        # fallback lookup by id if object identity failed for some reason
        if not kid_conflicts and kid.id in conflicting_ids:
            kid_conflicts = next((v for k, v in conflicts.items() if k.id == kid.id), [])
        kids_data.append({
            'kid': kid,
            'conflicts': kid_conflicts,
            'has_conflict': kid.id in conflicting_ids,
            'att_status': att_status_by_kid.get(kid.id),
        })

    context = {
        'invitation': invitation,
        'team_event': team_event,
        'selected_kids': selected_kids,   # still provided for any legacy bits
        'kids_data': kids_data,           # preferred for rendering: list of dicts
        'conflicts': conflicts,
        'att_status_by_kid': att_status_by_kid,
        'pending_kid_ids': pending_ids,
        'has_pending': bool(pending_ids),
        'total_selected': selected_kids.count(),
    }

    return render(request, 'core/resolve_team_event_conflict.html', context)

@login_required
def replace_with_team_event(request, team_event_invitation_id, kid_id):
    """Parent chose 'Replace & Attend' for a conflicting kid.
    - Handles family conflicts: remove kid (multi) or delete event (solo).
    - Handles team conflicts: decline the prior team attendance for the kid.
    - Creates (or flips to) accepted attendance for this team event.
    - Prunes the kid from pending list.
    - On last pending kid: finalize invitation.status (accepted if >=1 kid accepted overall), clean session, redirect.
    """
    try:
        invitation = TeamEventInvitation.objects.get(id=team_event_invitation_id)
        team_event = invitation.team_event
    except TeamEventInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect('notifications')

    if invitation.user != request.user:
        messages.error(request, "Unauthorized.")
        return redirect('notifications')

    user_family = request.user.profile.family
    try:
        chosen_kid = Kid.objects.get(id=kid_id, family=user_family)
    except Kid.DoesNotExist:
        messages.error(request, "Invalid kid.")
        return redirect('notifications')

    # Get (current) conflicts for this kid so we know what to clean up
    conflicts = get_kid_conflicts(chosen_kid, team_event)

    # Process each conflicting event (family or team)
    handled_count = 0
    for conflict in conflicts:
        old_event = conflict['event']

        if conflict['type'] == 'family':
            current_kids_count = old_event.kids.count()
            if current_kids_count > 1:
                old_event.kids.remove(chosen_kid)
            else:
                # Delete the family event entirely when it's the last/only kid being replaced.
                # This keeps the DB clean: replaced events are not kept as dead data.
                old_event.delete()
            handled_count += 1

        elif conflict['type'] == 'team':
            # Supersede the old team event: decline the prior attendance
            old_att = TeamEventAttendance.objects.filter(
                team_event=old_event,
                kid=chosen_kid,
                status='accepted'
            ).first()
            if old_att:
                old_att.status = 'declined'
                old_att.save()
                handled_count += 1

    # Accept this kid for the new team event (safe upsert)
    TeamEventAttendance.objects.update_or_create(
        team_event=team_event,
        kid=chosen_kid,
        defaults={'status': 'accepted'}
    )

    kid_full_name = f"{chosen_kid.first_name} {chosen_kid.last_name}".strip()

    if handled_count > 0:
        messages.success(request, f"Replaced {handled_count} conflicting event(s) and added {kid_full_name} to the team event.")
    else:
        messages.success(request, f"Successfully added {kid_full_name} to the team event.")

    # === Session / pending management (supports new keys + legacy) ===
    pending_key = 'team_event_pending_kids'
    original_key = 'team_event_original_selection'
    legacy_key = 'selected_kid_ids'

    pending_list = request.session.get(pending_key) or request.session.get(legacy_key, [])
    if pending_list:
        remaining = [str(k) for k in pending_list if int(k) != int(kid_id)]
        if remaining:
            request.session[pending_key] = remaining
            # keep original_selection for display
            return redirect('resolve_team_event_conflict', team_event_invitation_id=team_event_invitation_id)
        else:
            # Last kid processed in this wizard
            request.session.pop(pending_key, None)
            request.session.pop(original_key, None)
            request.session.pop(legacy_key, None)

    # Finalize invitation status: accepted if *any* of this parent's kids now have accepted attendance
    has_any_accepted = TeamEventAttendance.objects.filter(
        team_event=team_event,
        kid__family=user_family,
        status='accepted'
    ).exists()
    invitation.status = "accepted" if has_any_accepted else "declined"
    invitation.save()

    messages.success(request, "All conflict decisions processed.")
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


@login_required
def my_organization(request):
    """Convenience view for owners to see their own organization (used in nav)."""
    if request.user.profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    try:
        organization = Organization.objects.get(owner=request.user)
    except Organization.DoesNotExist:
        messages.error(request, "You don't have an organization yet.")
        return redirect('create_organization')

    context = {
        'organization': organization,
    }
    return render(request, 'core/organization_details.html', context)




    
    




    
            
