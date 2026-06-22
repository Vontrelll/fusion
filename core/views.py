from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from .models import (
    Kid, Event, Family, Invite, PlayerRegistration, Team, TeamEventInvitation,
    TeamMembership, Profile, Organization, TeamEvent, TeamEventAttendance,
    Notification, RosterRequestKid, AccountDeletionLog
)
from .utils import _safe_get_user_profile, _safe_get_user_family, _get_user_role
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.urls import reverse
from datetime import datetime, timedelta, date
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import User  # for family transfer logic in deletion (safe read)
from core.forms import CustomUserCreationForm, OrganizationForm, TeamEventForm, TIMEZONE_CHOICES
from . forms import TeamForm
from datetime import date, timedelta
from django.db.models import Q, Count
from django.db.models.functions import Lower
from django.core.paginator import Paginator
from collections import defaultdict

EVENTS_PER_PAGE = 10
ROSTER_PLAYERS_PER_PAGE = 3
TEAMS_PER_PAGE = 5
PLAYERS_PER_PAGE = 5
FIND_TEAMS_PER_PAGE = 5
FIND_TEAMS_MIN_QUERY_LEN = 2
import json
import logging
import pytz

# Audit logger for privacy-sensitive actions (account deletion)
deletion_logger = logging.getLogger('fusion.deletion')


# Create your views here.
# core/views.py


@login_required
def dashboard(request):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        return redirect('owner_dashboard')
    
    else:
        user_family = profile.family
        user_families = [user_family] if user_family else []

        kids = Kid.objects.filter(family=user_family) if user_family else Kid.objects.none()

        now = timezone.now()
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        in_one_week = now + timedelta(days=7)

        user_tz_str = profile.timezone if profile and profile.timezone else 'America/Chicago'
        try:
            user_tz = pytz.timezone(user_tz_str)
            local_now = now.astimezone(user_tz)
        except Exception:
            local_now = now

        parent_team_ids = list(
            TeamMembership.objects.filter(user=request.user, role='parent')
            .values_list('team_id', flat=True)
        )

        family_events_today = Event.objects.filter(
            family__in=user_families,
            start_time__date=today,
        ).prefetch_related('kids').order_by('start_time')

        team_events_today = _parent_accepted_team_events_for_date(
            user_family, kids, parent_team_ids, today
        )
        combined_today = _combine_parent_day_events(family_events_today, team_events_today)

        story_events = combined_today
        story_section_label = "Today's Events"
        story_focus_index = _parent_story_focus_index(story_events, now, [])

        has_live_today = any(_parent_event_is_live(ev) for ev in story_events)
        family_events_tomorrow = Event.objects.filter(
            family__in=user_families,
            start_time__date=tomorrow,
        ).prefetch_related('kids').order_by('start_time')
        team_events_tomorrow = _parent_accepted_team_events_for_date(
            user_family, kids, parent_team_ids, tomorrow
        )
        combined_tomorrow = _combine_parent_day_events(
            family_events_tomorrow, team_events_tomorrow
        )

        if has_live_today:
            story_focus_index = next(
                i for i, ev in enumerate(story_events) if _parent_event_is_live(ev)
            )
        elif not any(
            (ev.end_time >= now if ev.end_time else ev.start_time >= now)
            for ev in story_events
        ) and combined_tomorrow:
            story_events = combined_tomorrow
            story_section_label = "Tomorrow's Events"
            story_focus_index = 0

        tomorrow_events = combined_tomorrow[:5]

        family_week_count = Event.objects.filter(
            family__in=user_families,
            start_time__gte=now,
            start_time__lte=in_one_week,
        ).count()
        team_week_count = 0
        if parent_team_ids and kids:
            kid_ids = list(kids.values_list('id', flat=True))
            team_week_count = TeamEventAttendance.objects.filter(
                kid_id__in=kid_ids,
                status='accepted',
                team_event__team_id__in=parent_team_ids,
                team_event__start_time__gte=now,
                team_event__start_time__lte=in_one_week,
            ).values_list('team_event_id', flat=True).distinct().count()
        upcoming_count = family_week_count + team_week_count

        parent_teams_qs = Team.objects.filter(
            memberships__user=request.user,
            memberships__role='parent',
        ).select_related('organization').distinct()
        teams_count = parent_teams_qs.count()
        parent_teams = parent_teams_qs[:4]

        unread_notifications = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        recent_activity = []
        recent_notifs = Notification.objects.filter(user=request.user).order_by('-created_at')[:5]
        for n in recent_notifs:
            extra = n.extra_data or {}
            ntype = n.notification_type
            if ntype == 'team_event_invitation':
                invitation_id = extra.get('invitation_id') or extra.get('team_event_id')
                link = (
                    reverse('team_event_kid_selection', args=[invitation_id])
                    if invitation_id else reverse('notifications')
                )
            elif ntype == 'team_invite':
                invite_id = extra.get('invite_id')
                link = (
                    reverse('team_invite_response', args=[invite_id])
                    if invite_id else reverse('notifications')
                )
            elif ntype in ('family_invite', 'parent_invite', 'family_join_request'):
                invite_id = extra.get('invite_id')
                link = (
                    reverse('family_invite_response', args=[invite_id])
                    if invite_id else reverse('notifications')
                )
            elif ntype == 'team_event_updated':
                team_event_id = extra.get('team_event_id') or extra.get('event_id')
                link = (
                    reverse('review_team_event_update', args=[team_event_id])
                    if team_event_id else reverse('event_list')
                )
            elif ntype == 'team_event_canceled':
                link = reverse('event_list')
            else:
                link = reverse('notifications')
            recent_activity.append({
                'type': 'notification',
                'when': n.created_at,
                'text': n.title,
                'link': link,
            })

        context = {
            'num_of_kids': kids.count(),
            'num_of_events': len(combined_today),
            'upcoming_count': upcoming_count,
            'user_events': combined_today,
            'kids': kids,
            'parent_teams': parent_teams,
            'teams_count': teams_count,
            'family_name': user_family.family_name if user_family else '',
            'now': local_now,
            'story_events': story_events,
            'story_section_label': story_section_label,
            'story_focus_index': story_focus_index,
            'tomorrow_events': tomorrow_events,
            'unread_notifications': unread_notifications,
            'recent_activity': recent_activity,
        }

    return render(request, "core/dashboard.html", context)

@never_cache
@login_required
def owner_dashboard(request):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    # Role check - only allow owners
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "You do not have access to the Owner Dashboard.")
        return redirect('dashboard')  # or 'parent_dashboard' if you rename it

    # Get user's organization (assuming one for now)
    organization = Organization.objects.filter(owner=request.user).first()
    
    # Get teams under that organization
    teams = Team.objects.filter(organization=organization) if organization else []

    # Annotate with roster counts for owner visibility
    for team in teams:
        team.roster_count = PlayerRegistration.objects.filter(
            team_membership__team=team
        ).count()

    pending_requests = Invite.objects.filter(receiver=request.user,status="pending").count()

    # NEW: split pending for Action Center (roster requests incoming to owner, invites sent by owner)
    pending_roster_requests = Invite.objects.filter(
        receiver=request.user, status="pending", invite_type="team_join_request"
    ).count()
    pending_invites = Invite.objects.filter(
        sender=request.user, status="pending", invite_type="team_sent_invite"
    ).count()

    # Dynamic total players from actual registrations - UPDATED for unique/deduped players at org level
    total_players = PlayerRegistration.objects.filter(
        team_membership__team__organization=organization
    ).values('kid').distinct().count() if organization else 0

    num_families = PlayerRegistration.objects.filter(
        team_membership__team__organization=organization
    ).values('kid__family').distinct().count() if organization else 0

    # Stories row (today or tomorrow if today is done) + tomorrow's events list
    story_events = []
    story_section_label = "Today's Events"
    story_focus_index = 0
    tomorrow_events = []
    upcoming_count = 0
    if organization:
        now = timezone.now()
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        in_one_week = now + timedelta(days=7)

        def _attach_summaries(events):
            for ev in events:
                ev.summary = get_attendance_summary(ev)

        org_events = _owner_team_event_queryset(request.user).select_related('team')

        today_qs = org_events.filter(start_time__date=today).order_by('start_time')
        today_events = list(today_qs)
        _attach_summaries(today_events)

        story_events = today_events
        story_section_label = "Today's Events"
        story_focus_index = 0
        live_index = next((i for i, ev in enumerate(story_events) if ev.is_happening_now()), None)
        if live_index is not None:
            story_focus_index = live_index
        else:
            for i, ev in enumerate(story_events):
                if ev.end_time:
                    still_upcoming = ev.end_time >= now
                else:
                    still_upcoming = ev.start_time >= now
                if still_upcoming:
                    story_focus_index = i
                    break
            else:
                has_live_today = any(ev.is_happening_now() for ev in story_events)
                tomorrow_story = list(
                    org_events.filter(start_time__date=tomorrow).order_by('start_time')
                )
                if has_live_today:
                    story_focus_index = next(
                        i for i, ev in enumerate(story_events) if ev.is_happening_now()
                    )
                elif tomorrow_story:
                    story_events = tomorrow_story
                    story_section_label = "Tomorrow's Events"
                    story_focus_index = 0
                    _attach_summaries(story_events)
                elif story_events:
                    story_focus_index = len(story_events) - 1

        tomorrow_events = list(
            org_events.filter(start_time__date=tomorrow).order_by('start_time')[:5]
        )
        _attach_summaries(tomorrow_events)

        upcoming_count = org_events.filter(
            start_time__gte=now,
            start_time__lte=in_one_week,
        ).count()

    context = {
        'user_organization': organization,
        'total_teams': len(teams),
        'total_players': total_players,
        'num_families': num_families,
        'pending_requests': pending_requests,  
        'pending_roster_requests': pending_roster_requests,
        'pending_invites': pending_invites,
        'my_teams': teams,
        'tomorrow_events': tomorrow_events,
        'upcoming_count': upcoming_count,
        'story_events': story_events,
        'story_section_label': story_section_label,
        'story_focus_index': story_focus_index,
    }
    
    return render(request, "core/owner_dashboard.html", context)


@login_required
def organization_players(request):
    """Owner view: All players (registrations) across the organization.
    Shows kid name, team, family (clickable to parent contact details).
    Responsive for mobile.
    """
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    organization = Organization.objects.filter(owner=request.user).first()
    if not organization:
        messages.error(request, "No organization found. Create one first.")
        return redirect('create_organization')

    registrations_qs = PlayerRegistration.objects.filter(
        team_membership__team__organization=organization
    ).select_related(
        'kid', 'kid__family', 'kid__parent', 'team_membership__team'
    ).order_by('kid__last_name', 'kid__first_name', 'kid__id')

    total_players = registrations_qs.count()

    search_q = (request.GET.get('q') or '').strip()
    if search_q:
        registrations_qs = registrations_qs.filter(
            Q(kid__first_name__icontains=search_q) |
            Q(kid__last_name__icontains=search_q) |
            Q(team_membership__team__name__icontains=search_q) |
            Q(kid__family__family_name__icontains=search_q) |
            Q(kid__parent__first_name__icontains=search_q) |
            Q(kid__parent__last_name__icontains=search_q) |
            Q(kid__parent__username__icontains=search_q)
        )

    page_obj = Paginator(registrations_qs, PLAYERS_PER_PAGE).get_page(request.GET.get('page'))

    context = {
        'organization': organization,
        'page_obj': page_obj,
        'total_players': total_players,
        'search_q': search_q,
    }
    return render(request, "core/org_players.html", context)

    

#This section below is for all things Events related 
# Creating a new event. Connects to add_event.html
#THI ADD EVENT IS FOR FAMILIES ONLY
@login_required
def add_event(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "Only parents can create family events.")
        return redirect('owner_dashboard')

    user_family = profile.family
    user_kids = Kid.objects.filter(family=user_family) if user_family else Kid.objects.none()
    family_parents = user_family.parents.all() if user_family else []

    if request.method == "POST":
        name = (request.POST.get("name") or '').strip().title()
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        location = (request.POST.get("location") or '').strip().title()
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
    if _get_user_role(request) != "owner":
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

            if not team_event.event_type:
                team_event.event_type = 'team'

            # For training sessions, team is optional (sent to individual players)
            if team_event.event_type == 'training' and not team_event.team:
                team_event.team = None

            # === CONFLICT CHECK FOR TEAM (skip if no team for training) ===
            if team_event.team and has_conflict(team_event, team=team_event.team):
                messages.error(request, "Time conflict detected. This team already has an event at this time.")
                return render(request, "core/add_team_event.html", {"form": form})

            team_event.save()

            # === Branch: Team Event (broadcast) vs Training Session (selective invites) ===
            if team_event.event_type == 'training':
                messages.success(request, f"Training session '{team_event.name}' created. Now select which players to invite.")
                return redirect('select_players_for_training', event_id=team_event.id)
            else:
                # Create invitations for ALL parents on the team (existing behavior)
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
        # Support preselect via ?team=123 from team_detail or dashboard CTAs
        preselect_team_id = request.GET.get('team')
        if preselect_team_id:
            try:
                form.fields['team'].initial = int(preselect_team_id)
            except (ValueError, TypeError):
                pass
        # Support type preselect for training sessions
        preselect_type = request.GET.get('type')
        if preselect_type in ('team', 'training'):
            form.fields['event_type'].initial = preselect_type

    return render(request, 'core/add_team_event.html', {'form': form})


@login_required
def select_players_for_training(request, event_id):
    """Owner selects specific kids/players to invite to a training session.
    Creates invitations (per parent) + pre-creates 'pending' attendances (per kid)
    so the owner can later see exactly who was invited and their response status.
    Parents experience the exact same invitation flow as team events.
    """
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "Only team owners can invite players to training sessions.")
        return redirect('dashboard')

    try:
        team_event = TeamEvent.objects.get(
            id=event_id,
            created_by=request.user
        )
    except TeamEvent.DoesNotExist:
        messages.error(request, "Training session not found or you do not have access.")
        return redirect('event_list')

    if team_event.event_type != 'training':
        messages.error(request, "This event is not a training session.")
        return redirect('team_event_detail', event_id=event_id)

    # For training without team, use owner's org
    organization = None
    if team_event.team:
        organization = team_event.team.organization
    else:
        organization = Organization.objects.filter(owner=request.user).first()

    if request.method == "POST":
        selected_kid_ids = request.POST.getlist('kids')

        if not selected_kid_ids:
            messages.error(request, "Please select at least one player to invite.")
        else:
            # Validate kids belong to this owner's organization rosters
            selected_kids = list(
                Kid.objects.filter(
                    id__in=selected_kid_ids,
                    playerregistration__team_membership__team__organization=organization
                ).select_related('parent').distinct()
            )

            if not selected_kids:
                messages.error(request, "Invalid player selection.")
            else:
                invited_kid_count = 0
                notified_parent_ids = set()

                for kid in selected_kids:
                    parent = kid.parent

                    # Create (or get) invitation for the parent (parents get the request)
                    invitation, _ = TeamEventInvitation.objects.get_or_create(
                        team_event=team_event,
                        user=parent,
                        defaults={'status': 'pending'}
                    )

                    # Pre-create pending attendance for THIS specific invited kid.
                    # This lets owners see exactly who they invited + current status.
                    TeamEventAttendance.objects.get_or_create(
                        team_event=team_event,
                        kid=kid,
                        defaults={'status': 'pending'}
                    )

                    invited_kid_count += 1

                    # Notify parent once (even if multiple of their kids invited)
                    if parent.id not in notified_parent_ids:
                        notified_parent_ids.add(parent.id)
                        source_label = _training_invite_source_label(team_event, organization)
                        extra_data = {
                            'team_event_id': team_event.id,
                            'invitation_id': invitation.id,
                        }
                        if team_event.team_id:
                            extra_data['team_id'] = team_event.team_id
                        Notification.objects.get_or_create(
                            user=parent,
                            notification_type='team_event_invitation',
                            extra_data__team_event_id=team_event.id,
                            defaults={
                                'title': f"Training Invitation: {team_event.name}",
                                'message': (
                                    f"You have been invited to the training session "
                                    f"'{team_event.name}' by {source_label}."
                                ),
                                'extra_data': extra_data,
                            }
                        )

                messages.success(
                    request,
                    f"Training session '{team_event.name}' ready. Invited {invited_kid_count} player(s). Parents have received requests."
                )
                return redirect('team_event_detail', event_id=team_event.id)

    # GET: list eligible players from the org
    if organization:
        registrations = PlayerRegistration.objects.filter(
            team_membership__team__organization=organization
        ).select_related(
            'kid', 'kid__parent', 'team_membership__team'
        ).order_by('kid__last_name', 'kid__first_name')
    else:
        registrations = PlayerRegistration.objects.none()

    context = {
        'team_event': team_event,
        'registrations': registrations,
    }
    return render(request, "core/training_player_selection.html", context)





#EDIT TEAM EVENTS 
@login_required
def edit_event(request, event_id):
    profile = _safe_get_user_profile(request)
    user_family = profile.family

    if profile.role != "parent":
        messages.error(request, 'You do not have permission to edit this event.')
        return redirect('owner_dashboard')

    # Scope the lookup to this user's family (prevents IDOR / data leakage for guessed IDs)
    try:
        event = Event.objects.get(id=event_id, family=user_family)
    except Event.DoesNotExist:
        messages.error(request, "Event not found.")
        return redirect('event_list')

    # Initialize for GET and failed POST
    selected_kids = event.kids.all()
    selected_attending_parents = event.attending_parents.all()

    if request.method == "POST":
        name = (request.POST.get("name") or '').strip().title()
        location = (request.POST.get("location") or '').strip().title()
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
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, 'You do not have permission to edit this event.')
        return redirect('dashboard')

    # Hardened lookup: only events belonging to this owner's teams (prevents other owners editing via ID guess)
    try:
        team_event = TeamEvent.objects.get(
            id=event_id,
            team__organization__owner=request.user
        )
    except TeamEvent.DoesNotExist:
        messages.error(request, "This team event does not exist or you do not have access.")
        return redirect('event_list')

    user_teams = Team.objects.filter(organization__owner=request.user)

    if request.method == "POST":
        name = (request.POST.get('name') or '').strip().title()
        start_time_str = request.POST.get('start_time')
        end_time_str = request.POST.get('end_time')
        location = (request.POST.get('location') or '').strip().title()
        description = request.POST.get('description')
        team_id = request.POST.get('team')

        try:
            team = Team.objects.get(id=team_id, organization__owner=request.user)
        except Team.DoesNotExist:
            messages.error(request, 'Selected team does not exist or is not yours.')
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

        # Preserve or update event type (team vs training)
        new_event_type = request.POST.get('event_type')
        if new_event_type in ('team', 'training'):
            team_event.event_type = new_event_type

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
        # Any event involving these kids (family-wide). A kid's calendar is blocked
        # regardless of which parent in the family created the prior event.
        personal_events = Event.objects.filter(
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
    ONLY checks events the parent has explicitly confirmed/added to calendar (personal Events
    they created + accepted TeamEventAttendance). Does NOT check pending invites, declined,
    or unconfirmed items (they are not yet on the calendar).
    """
    if not proposed_event.start_time or not proposed_event.end_time:
        return []

    conflicts = []

    # 1. Check Family/Personal Events for this kid (explicitly created by parent).
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

    # 2. Check Accepted Team Events the kid is already attending (other than this one).
    # Only confirmed/attending events.
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

    return conflicts


def get_conflicts_for_kids(selected_kids, proposed_event):
    """
    Returns a dictionary of kids who have conflicts.
    Format: {kid: [list of conflict dicts]}
    Keys are the Kid model instances (supports 'kid in conflicts' and conflicts[kid]).

    Thin wrapper around get_kid_conflicts. Only confirmed/attending events are considered.
    """
    conflicts = {}
    for kid in selected_kids:
        kid_conflicts = get_kid_conflicts(kid, proposed_event)
        if kid_conflicts:
            conflicts[kid] = kid_conflicts
    return conflicts


def _parent_event_is_live(event):
    """True when any parent-dashboard event (family or team) is in progress."""
    now = timezone.now()
    return event.start_time <= now <= event.end_time


def _parent_accepted_team_events_for_date(user_family, kids, parent_team_ids, target_date):
    """Team events on a given day where this family's kids have accepted attendance."""
    if not parent_team_ids or not kids:
        return []
    kid_ids = list(kids.values_list('id', flat=True))
    accepted_event_ids = TeamEventAttendance.objects.filter(
        kid_id__in=kid_ids,
        status='accepted',
        team_event__team_id__in=parent_team_ids,
        team_event__start_time__date=target_date,
    ).values_list('team_event_id', flat=True).distinct()
    if not accepted_event_ids:
        return []
    team_events = list(
        TeamEvent.objects.filter(id__in=accepted_event_ids)
        .select_related('team')
        .order_by('start_time')
    )
    for ev in team_events:
        ev.attending_kids = Kid.objects.filter(
            teameventattendance__team_event=ev,
            teameventattendance__status='accepted',
            family=user_family,
        ).distinct()
    return team_events


def _combine_parent_day_events(family_events_qs, team_events_list):
    """Merge family + accepted team events for one day, sorted by start time."""
    combined = []
    for ev in family_events_qs:
        ev.is_team_event = False
        ev.event_type = 'family'
        ev.attending_kids = list(ev.kids.all())
        ev.is_live = _parent_event_is_live(ev)
        combined.append(ev)
    for ev in team_events_list:
        ev.is_team_event = True
        ev.is_live = _parent_event_is_live(ev)
        combined.append(ev)
    combined.sort(key=lambda e: e.start_time)
    return combined


def _parent_story_focus_index(story_events, now, tomorrow_story):
    """Pick the story carousel focus index (live first, else next upcoming)."""
    story_focus_index = 0
    live_index = next(
        (i for i, ev in enumerate(story_events) if _parent_event_is_live(ev)),
        None,
    )
    if live_index is not None:
        return live_index
    for i, ev in enumerate(story_events):
        if ev.end_time:
            still_upcoming = ev.end_time >= now
        else:
            still_upcoming = ev.start_time >= now
        if still_upcoming:
            return i
    if story_events:
        return len(story_events) - 1
    return 0


def _owner_team_event_queryset(user):
    """Team events an owner can manage (team-based or org-wide training they created)."""
    return TeamEvent.objects.filter(
        Q(team__organization__owner=user) |
        Q(created_by=user, team__isnull=True)
    )


def _user_owns_team_event(team_event, user):
    if team_event.team_id:
        return team_event.team.organization.owner_id == user.id
    return team_event.created_by_id == user.id


def _training_invite_source_label(team_event, organization=None):
    if team_event.team_id:
        return team_event.team.name
    if organization:
        return organization.name
    return "your organization"


def get_attendance_summary(team_event):
    """Safe summary for team event attendance (used by owners for roster planning and visibility).
    Returns counts + filtered lists. Does not leak across families.
    Now includes 'pending' (used for training sessions to show invited but not-yet-responded players).
    """
    qs = team_event.attendances.select_related('kid', 'kid__family').all()
    accepted = [a for a in qs if a.status == 'accepted']
    declined = [a for a in qs if a.status == 'declined']
    pending = [a for a in qs if a.status == 'pending']
    needs_review = [a for a in qs if getattr(a, 'needs_review', False)]
    return {
        'accepted_count': len(accepted),
        'declined_count': len(declined),
        'pending_count': len(pending),
        'needs_review_count': len(needs_review),
        'accepted': accepted,
        'declined': declined,
        'pending': pending,
        'total': len(qs),
    }


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
    

    profile = _safe_get_user_profile(request)
    user_family = profile.family

    # Owners should not hit the parent "review update" flow; send them to event details
    if profile.role == 'owner' or not user_family or TeamEvent.objects.filter(
            id=event_id, team__organization__owner=request.user).exists():
        return redirect('team_event_detail', event_id=event_id)

    attendances = list(TeamEventAttendance.objects.filter(
                team_event=team_event,
                kid__family=user_family,
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
        profile = _safe_get_user_profile(request)
        user_family = profile.family
        TeamEventAttendance.objects.filter(
            team_event=event,
            kid__family=user_family,
            status='accepted'
        ).update(needs_review=False)
        
        messages.success(request, f"You kept '{event.name}'. It will remain on your calendar with the updated details.")
        
    except TeamEvent.DoesNotExist:
        messages.error(request, "This event no longer exists.")
    
    return redirect('event_list')


@login_required
def remove_team_event_attendance(request, event_id):
    """Parent chooses to remove the team event from their calendar (self-unregister)."""
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response

    profile = _safe_get_user_profile(request)
    user_family = profile.family
    try:
        # Scope not strictly needed (we check attendance), but use try for safety
        event = TeamEvent.objects.get(id=event_id)
        
        # Remove the attendance record (only for this parent's kids)
        attendances = TeamEventAttendance.objects.filter(
            team_event=event,
            kid__parent=request.user
        )
        removed_names = []
        for att in list(attendances):
            kid_name = f"{att.kid.first_name} {att.kid.last_name}".strip()
            removed_names.append(kid_name)
            att.delete()
        
        if removed_names:
            messages.success(request, f"'{event.name}' has been removed from your calendar for: {', '.join(removed_names)}.")
            # Notify owner of the un-registration
            owner = event.team.organization.owner
            if owner and owner != request.user:
                Notification.objects.create(
                    user=owner,
                    title="Attendance Change",
                    message=f"{', '.join(removed_names)} removed from '{event.name}' by parent.",
                    notification_type='team_event_updated',
                    extra_data={'team_event_id': event.id, 'action': 'unregistered_by_parent'}
                )
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
        profile = _safe_get_user_profile(request)
        user_family = profile.family
        try:
            event = Event.objects.get(id=event_id, family=user_family)
        except Event.DoesNotExist:
            messages.error(request, "Event not found.")
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
        profile = _safe_get_user_profile(request)
        try:
            team_event = TeamEvent.objects.get(
                id=team_event_id,
                team__organization__owner=request.user
            )
        except TeamEvent.DoesNotExist:
            messages.error(request, "Team event not found.")
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



def _event_list_range(request):
    """Validate ?range= for owner event list filters."""
    range_param = request.GET.get('range', 'upcoming')
    if range_param not in ('upcoming', 'past'):
        return 'upcoming'
    return range_param


def _apply_event_time_range(queryset, range_param):
    """Filter events by upcoming or past."""
    now = timezone.now()
    if range_param == 'upcoming':
        return queryset.filter(end_time__gte=now)
    return queryset.filter(end_time__lt=now)


def _parent_calendar_events(user_family, range_param):
    """Merge family events + accepted team events for the parent calendar."""
    user_families = [user_family] if user_family else []
    now = timezone.now()

    personal_events = list(Event.objects.filter(
        family__in=user_families,
    ).prefetch_related('kids', 'family', 'created_by'))

    accepted_team_events = []
    if user_family:
        accepted_team_events = list(TeamEvent.objects.filter(
            attendances__kid__family=user_family,
            attendances__status='accepted',
        ).select_related('team').distinct())

        team_event_ids = [event.id for event in accepted_team_events]
        attendances_by_event = defaultdict(list)
        if team_event_ids:
            for attendance in TeamEventAttendance.objects.filter(
                team_event_id__in=team_event_ids,
                kid__family=user_family,
                status='accepted',
            ).select_related('kid'):
                attendances_by_event[attendance.team_event_id].append(attendance)

        for event in accepted_team_events:
            user_attendances = attendances_by_event.get(event.id, [])
            if user_attendances:
                event.user_attendances = user_attendances

    for ev in personal_events:
        ev.is_team_event = False
        ev.event_type = 'family'
        ev.is_happening_now = _parent_event_is_live(ev)
        ev.attendance_count = ev.kids.count()
    for ev in accepted_team_events:
        ev.is_team_event = True
        ev.is_happening_now = _parent_event_is_live(ev)
        ev.attendance_count = len(getattr(ev, 'user_attendances', []))

    combined = personal_events + accepted_team_events
    if range_param == 'upcoming':
        combined = [e for e in combined if e.end_time >= now]
        combined.sort(key=lambda x: x.start_time)
    else:
        combined = [e for e in combined if e.end_time < now]
        combined.sort(key=lambda x: x.start_time, reverse=True)
    return combined


@login_required
def event_list(request):
    # Mark specific notif as read if clicked from notifications (e.g. for canceled team events)
    # If ?read present, this returns redirect to clean URL (no ?read)
    response = _mark_notification_as_read(request)
    if response:
        return response

    profile = _safe_get_user_profile(request)
    if profile.role == "parent":
        range_param = _event_list_range(request)
        all_events = _parent_calendar_events(profile.family, range_param)
        page_obj = Paginator(all_events, EVENTS_PER_PAGE).get_page(request.GET.get('page'))

        context = {
            'events': page_obj,
            'page_obj': page_obj,
            'range_filter': range_param,
            'is_parent': True,
        }

    else:  # Owner
        range_param = _event_list_range(request)
        events_qs = _owner_team_event_queryset(request.user).select_related('team').annotate(
            attendance_count=Count(
                'attendances',
                filter=Q(attendances__status='accepted'),
            ),
            pending_count=Count(
                'attendances',
                filter=Q(attendances__status='pending'),
            ),
        )
        events_qs = _apply_event_time_range(events_qs, range_param)
        if range_param == 'past':
            events_qs = events_qs.order_by('-start_time')
        else:
            events_qs = events_qs.order_by('start_time')

        page_obj = Paginator(events_qs, EVENTS_PER_PAGE).get_page(request.GET.get('page'))
        context = {
            'events': page_obj,
            'page_obj': page_obj,
            'range_filter': range_param,
            'is_parent': False,
        }

    return render(request, 'core/event_list.html', context)



#This section below is the AUTHENTICATION section


def _apply_no_cache_headers(response):
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def csrf_failure(request, reason=""):
    """Send users back to login with a fresh token instead of a raw 403 page."""
    return redirect(f"{reverse('login')}?csrf=1")


@never_cache
@ensure_csrf_cookie
def login_view(request):
    # If already logged in, don't show login form (prevents back-button issues after login)
    if request.user.is_authenticated:
        profile = _safe_get_user_profile(request)
        if profile and profile.role == 'owner':
            return redirect('owner_dashboard')
        return redirect('dashboard')

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password")
        user = None

        if username and password:
            # Username login is case-insensitive; only password is case-sensitive
            try:
                user_obj = User.objects.get(username__iexact=username)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                user = None

        if user:
            login(request, user)
            # Rotate the CSRF token after login to invalidate any old tokens from cached forms
            from django.middleware.csrf import rotate_token
            rotate_token(request)
            # Use role-aware redirect
            profile = _safe_get_user_profile(request)
            if profile and profile.role == 'owner':
                return redirect('owner_dashboard')
            return redirect('dashboard')

        else:
            return _apply_no_cache_headers(render(request, "core/login.html", context={
                "error": "Invalid credentials, try again."
            }))

    # Privacy: show friendly message after successful account deletion
    deleted = request.GET.get('deleted') == '1'
    csrf_error = request.GET.get('csrf') == '1'
    context = {}
    if deleted:
        context['success'] = "Your account and all associated data have been permanently deleted. We're sorry to see you go."
    if csrf_error:
        context['csrf_error'] = (
            "Your session expired or the page was cached. Please log in again."
        )

    return _apply_no_cache_headers(render(request, "core/login.html", context))


@never_cache
@login_required
def logout_view(request):
    logout(request)
    response = redirect('login')
    return _apply_no_cache_headers(response)


def signup_view(request):
    if request.method == "POST":
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()  # This already creates the Profile via form/middleware

            # IMPORTANT FIX: Specify backend when using multiple auth backends
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')

            # Use direct on the fresh user
            if user.profile.role == 'owner':
                return redirect('owner_onboarding')
            else:
                return redirect('create_first_family')
    else:
        form = CustomUserCreationForm()

    return render(request, "core/signup.html", {'form': form})


# Re-export for account_settings and any other modules that need the list.
# The canonical definition lives in forms.py so signup and settings stay in sync.
COMMON_TIMEZONES = TIMEZONE_CHOICES



@login_required
def account_settings(request):
    profile = _safe_get_user_profile(request)
    
    if request.method == "POST":
        # Update User model fields
        request.user.first_name = (request.POST.get('first_name', request.user.first_name) or '').strip().title()
        request.user.last_name = (request.POST.get('last_name', request.user.last_name) or '').strip().title()
        new_email = (request.POST.get('email', request.user.email) or '').strip()
        if new_email and User.objects.filter(email__iexact=new_email).exclude(id=request.user.id).exists():
            messages.error(request, "An account with this email address already exists.")
            return redirect(reverse('account_settings') + '?edit=1')
        request.user.email = new_email
        request.user.save()

        # Update Profile fields
        new_tz = request.POST.get('timezone')
        # Only accept known IANA keys from our list (prevents garbage from old template or tampering)
        tz_keys = {tz[0] for tz in COMMON_TIMEZONES}
        if new_tz in tz_keys and profile.timezone != new_tz:
            profile.timezone = new_tz
            profile.save(update_fields=['timezone'])

        # Phone Number (enforce model max_length=40 to avoid DataError)
        phone = (request.POST.get('phone', '') or '').strip()[:40]
        if profile.phone != phone:
            profile.phone = phone
            profile.save(update_fields=['phone'])

        messages.success(request, "✅ Profile updated successfully!")
        return redirect('account_settings')

    current_timezone = profile.timezone if profile else 'America/Chicago'
    timezone_label = next(
        (label for key, label in COMMON_TIMEZONES if key == current_timezone),
        current_timezone,
    )

    return render(request, "core/account_settings.html", {
        'user': request.user,
        'profile': profile,
        'current_timezone': current_timezone,
        'timezone_label': timezone_label,
        'timezones': COMMON_TIMEZONES,
        'editing': request.GET.get('edit') == '1',
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
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    user_family = profile.family

    if request.method == "POST":
        first_name = (request.POST.get("first_name") or '').strip().title()
        last_name = (request.POST.get("last_name") or '').strip().title()
        date_of_birth = request.POST.get("date_of_birth")
        gender = request.POST.get("gender")

        #CHECK IF USER IS PART OF A FAMILY
        if not user_family:
            messages.error(request, "You must be part of a family before adding kids.")
            return redirect('setup_family')
        #CHECK IF USER IS PART OF THE FAMILY THE KID IS BEING ADDED TO
        if user_family != profile.family:
            messages.error(request, "You do not have permission to add kids to this family.")
            return redirect('my_family')
            
   
        #creating the kid and saving it to the parent and database
        kid = Kid(first_name=first_name, last_name=last_name, date_of_birth=date_of_birth, 
        gender=gender)
        kid.parent = request.user
        kid.family = user_family

        # Assign a super distinct color (not same blue for all kids)
        existing_colors = set(Kid.objects.filter(family=user_family).values_list('color', flat=True))
        palette = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6', '#06b6d4', '#3b82f6', '#6366f1', '#8b5cf6', '#d946ef', '#f43f5e', '#84cc16']
        for c in palette:
            if c not in existing_colors:
                kid.color = c
                break
        else:
            kid.color = '#3b82f6'  # fallback blue if all used
        kid.save()
        # If user came from family page to add kid, return there; else default my_family (or could use kid_list)
        if request.GET.get('from') == 'family':
            return redirect('my_family')
        return redirect('my_family')

    #GET request
    return render(request, "core/add_kid.html")



@login_required
def edit_kid(request, kid_id):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    user_family = profile.family
    try:
        kid = Kid.objects.get(id=kid_id, family=user_family)
    except Kid.DoesNotExist:
        messages.error(request, "Kid not found.")
        return redirect('my_family')

    if request.method == "POST":
        kid.first_name = (request.POST.get("first_name") or '').strip().title()
        kid.last_name = (request.POST.get("last_name") or '').strip().title()
        kid.date_of_birth = request.POST.get("date_of_birth")
        kid.gender = request.POST.get("gender")
        kid.save()
        messages.success(request, "Kid information updated successfully.")
        return redirect('my_family')
    
    return render(request, "core/edit_kid.html", {"kid": kid})

@login_required
def delete_kid(request, kid_id):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    user_family = profile.family
    try:
        kid = Kid.objects.get(id=kid_id, family=user_family)
    except Kid.DoesNotExist:
        messages.error(request, "This kid does not exist.")
        return redirect('kid_list')

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
        return redirect('my_family')            # or 'kid_list'

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

    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    user_family = profile.family
    # (removed dead self-comparison redirects that did nothing)
    if user_family:
        kids = Kid.objects.filter(family=user_family)
    else:
        kids = Kid.objects.none()

    # Backfill distinct colors for old kids so they don't all have the same default color
    if kids:
        existing_colors = set(kids.values_list('color', flat=True))
        palette = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6', '#06b6d4', '#3b82f6', '#6366f1', '#8b5cf6', '#d946ef', '#f43f5e', '#84cc16']
        updated = False
        for kid in kids:
            if not kid.color or kid.color == '#3b82f6' or kid.color not in palette:  # treat default or missing as needing color
                for c in palette:
                    if c not in existing_colors:
                        kid.color = c
                        kid.save(update_fields=['color'])
                        existing_colors.add(c)
                        updated = True
                        break
                else:
                    kid.color = '#3b82f6'
                    kid.save(update_fields=['color'])
        if updated:
            # refresh queryset
            kids = Kid.objects.filter(family=user_family)

    return render(request, "core/kid_list.html", context={"kids": kids})


#From here down is family views
@login_required
def add_family(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    if request.method == "POST":
        family_name = (request.POST.get("family_name") or '').strip().title()
        new_family = Family(family_name=family_name, created_by=request.user)
        new_family.save()
        profile.family = new_family
        profile.save()
        return redirect('dashboard')
    
    return render(request, "core/add_family.html")


#Family List
@login_required
def family_list(request):
    # Legacy URL kept for backward compatibility (bookmarks, old links).
    # All internal flows now use my_family (which renders the family details).
    # This safely redirects without breaking any URLs.
    return redirect('my_family')


@login_required
def my_family(request):
    """Directly shows the current user's single family details."""
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    family = profile.family
    if not family:
        messages.error(request, "You don't have a family yet.")
        return redirect('create_first_family')

    # Backfill distinct colors for old kids (so they get unique colors even if created before the feature)
    kids_qs = family.kids.all()
    if kids_qs.exists():
        existing_colors = set(kids_qs.values_list('color', flat=True))
        palette = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6', '#06b6d4', '#3b82f6', '#6366f1', '#8b5cf6', '#d946ef', '#f43f5e', '#84cc16']
        for kid in kids_qs:
            if not kid.color or kid.color == '#3b82f6':
                for c in palette:
                    if c not in existing_colors:
                        kid.color = c
                        kid.save(update_fields=['color'])
                        existing_colors.add(c)
                        break
                else:
                    kid.color = '#3b82f6'
                    kid.save(update_fields=['color'])

    # Reuse the same logic as family_detail for pending invites etc.
    pending_invites = Invite.objects.filter(
        family=family,
        status="pending"
    ).select_related('sender', 'receiver').order_by('-created_at')

    kids = family.kids.all().order_by('first_name', 'last_name')
    parents = family.parents.all().order_by('first_name', 'last_name')

    context = {
        "family": family,
        "pending_invites": pending_invites,
        "kids": kids,
        "parents": parents,
        "is_owner_view": False,
    }
    return render(request, "core/family_details.html", context)


@login_required
def parent_teams(request):
    """Page for parents to see all teams they belong to."""
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    memberships = TeamMembership.objects.filter(
        user=request.user,
        role='parent'
    ).select_related('team', 'team__organization').order_by('team__name')

    teams = []
    family = profile.family
    for membership in memberships:
        team = membership.team
        if family:
            team.registered_kids_count = PlayerRegistration.objects.filter(
                team_membership__team=team,
                kid__family=family,
            ).count()
        else:
            team.registered_kids_count = 0
        teams.append(team)

    pending_join_count = Invite.objects.filter(
        sender=request.user,
        status='pending',
        invite_type='team_join_request',
    ).count()

    context = {
        "teams": teams,
        "teams_count": len(teams),
        "pending_join_count": pending_join_count,
    }
    return render(request, "core/parent_teams.html", context)


@login_required
def remove_kid_from_team(request, team_id, kid_id):
    """Parent removes one of their kids from a team's roster (unregister from team)."""
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    try:
        kid = Kid.objects.get(id=kid_id, parent=request.user)
        team = Team.objects.get(id=team_id)
        # Verify parent is member of the team
        membership = TeamMembership.objects.get(team=team, user=request.user, role='parent')
        deleted, _ = PlayerRegistration.objects.filter(
            team_membership=membership, kid=kid
        ).delete()
        if deleted:
            messages.success(request, f"{kid.first_name} has been removed from the team {team.name}.")
            # Notify owner
            owner = team.organization.owner
            if owner and owner != request.user:
                Notification.objects.create(
                    user=owner,
                    title="Roster Change",
                    message=f"{kid.first_name} {kid.last_name} was removed from {team.name} by the parent.",
                    notification_type='roster_request',
                    extra_data={'team_id': team.id, 'kid_id': kid.id, 'action': 'removed_by_parent'}
                )
            # If no more registrations for this parent on the team, remove membership
            if not PlayerRegistration.objects.filter(team_membership=membership).exists():
                membership.delete()
                messages.info(request, f"You have left the team {team.name}.")
        else:
            messages.info(request, f"{kid.first_name} was not registered on that team.")
    except (Kid.DoesNotExist, Team.DoesNotExist, TeamMembership.DoesNotExist):
        messages.error(request, "Could not find the team or kid registration.")
    return redirect('parent_teams')


#Create first Family
@login_required
def create_first_family(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    # If user already has a family, redirect to dashboard
    if profile.family:
        return redirect('dashboard')
    
    if request.method == "POST":
        family_name = (request.POST.get("family_name") or '').strip().title()
        new_family = Family(family_name=family_name, created_by=request.user)
        new_family.save()
        profile.family = new_family
        profile.save()
        return redirect('dashboard')
    
    return render(request, "core/create_first_family.html")

#SETUP FAMILY
@login_required
def setup_family(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    return render(request, "core/setup_family.html")


@login_required
def parent_onboarding(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')
    return render(request, "core/parent_onboarding.html")


@login_required
def owner_onboarding(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')
    return render(request, "core/owner_onboarding.html")


#ALL THINGS INVITE RELATED. FOR PARENT TO PARENT INVITE/REQUEST AS WELL AS TEAM TO PARENT AND PARENT TO TEAM INVITE/REQUEST


#JOIN FAMILY LOGIC FOR SIGNUP FLOW
@login_required
def join_family(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
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
            current_family = _safe_get_user_family(request)
            if current_family and target_user.profile.family == family:
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

    # Defensive checks for already members (use .profile on the other users is fine here; enforce they are parents)
    receiver_profile = invite.receiver.profile
    sender_profile = invite.sender.profile

    # Enforce: only parents can be in families. Owners have no family side.
    if receiver_profile.role != 'parent' or sender_profile.role != 'parent':
        invite.delete()
        messages.error(request, "Family membership is only for parent accounts, not organization owners.")
        return redirect('notifications')

    receiver_in_family = receiver_profile.family
    sender_in_family = sender_profile.family

    if invite.invite_type == "sent_invite" and receiver_in_family:
        invite.delete()
        messages.info(request, "You were already a member of this family.")
        return redirect('family_detail', family_id=invite.family.id)

    if invite.invite_type == "family_join_request" and sender_in_family:
        invite.delete()
        messages.info(request, "This parent is already a member of the family.")
        return redirect('family_detail', family_id=invite.family.id)

    # Handle family invite types - set via the profile instance we fetched
    if invite.invite_type in ("join_request", "family_join_request"):
        sender_profile.family = invite.family
        sender_profile.save()

    elif invite.invite_type == "sent_invite":
        receiver_profile.family = invite.family
        receiver_profile.save()

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
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    user_family = profile.family
    try:
        family = Family.objects.get(id=family_id)
    except Family.DoesNotExist:
        messages.error(request, "Family does not exist")
        return redirect('my_family')

    if not user_family or user_family.id != family.id:
        messages.error(request, "You do not have access to this family.")
        return redirect('my_family')

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
    if _get_user_role(request) != "owner":
        messages.error(request, "You are not authorized to send this invite.")
        return redirect('dashboard')

    try:
        team = Team.objects.get(id=team_id, organization__owner=request.user)
    except Team.DoesNotExist:
        messages.error(request, "Team does not exist.")
        return redirect('team_list')

    try:
        target_user = User.objects.get(username__iexact=username)
    except User.DoesNotExist:
        messages.error(request, "User does not exist.")
        return redirect('team_to_parent_invite_search', team_id=team_id)

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
    return redirect('team_to_parent_invite_search', team_id=team.id)


def get_unregistered_kids_for_team(parent_user, team):
    """Return the kids in the parent's family that are not yet registered on this specific team.
    Owners have no family, so return empty for them.
    """
    if not parent_user or not team:
        return Kid.objects.none()

    p = parent_user.profile
    if p.role == 'owner' or not p.family:
        return Kid.objects.none()

    user_family = p.family

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
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "Only parents can select kids for team roster requests.")
        return redirect('owner_dashboard')

    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        messages.error(request, "Team does not exist.")
        return redirect('find_teams')

    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
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

    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
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
    if _get_user_role(request) == "parent":
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

    family = _safe_get_user_family(request)

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

    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "Owners cannot participate in family membership. This is a parent/family feature.")
        return redirect('owner_dashboard')

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
    # For join requests, the viewer is the family owner (a parent), who is expected to already be in the family.
    user_family = _safe_get_user_family(request)
    if invite.invite_type in ("sent_invite",) and user_family and user_family == family:
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

    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

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
            return redirect('team_detail', team_id=team.id)

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
    """Only the family creator (a parent) can remove other parents from the family.
    Families are strictly the parent/kid side; owners have no families.
    Hardened: scope lookups, require ownership, always define context.
    """
    profile = _safe_get_user_profile(request)
    user_family = profile.family

    try:
        family = Family.objects.get(id=family_id)
        parent = User.objects.get(id=parent_id)
    except (Family.DoesNotExist, User.DoesNotExist):
        messages.error(request, "Family or parent not found.")
        return redirect('my_family')

    # Authorization: must be the creator of this family, and the target must be in it.
    if request.user != family.created_by:
        messages.error(request, "You do not have permission to modify this family.")
        return redirect('my_family')

    if user_family is None or user_family.id != family.id:
        messages.error(request, "You do not have access to this family.")
        return redirect('my_family')

    parent_profile = parent.profile
    if parent_profile.family != family:
        messages.error(request, "That parent is not in this family.")
        return redirect('my_family')

    if request.method == "POST":
        if parent_profile.family == family:
            parent_profile.family = None
            parent_profile.save()
        if parent == family.created_by:
            family.delete()
        return redirect('my_family')

    context = {"parent": parent, "family": family}
    return render(request, "core/remove_parent.html", context)

@login_required
def family_detail(request, family_id):
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    profile = _safe_get_user_profile(request)
    family = Family.objects.filter(id=family_id).first()
    if not family:
        messages.error(request, "Family not found.")
        return redirect('dashboard')

    if profile.role == "parent":
        # Parents can only see their own family
        if not profile.family or profile.family.id != family.id:
            messages.error(request, "You do not have access to this family.")
            return redirect('my_family')
    elif profile.role == "owner":
        # Owners can view families of kids registered in their organization (for roster/parent contact access)
        has_connection = PlayerRegistration.objects.filter(
            kid__family=family,
            team_membership__team__organization__owner=request.user
        ).exists()
        if not has_connection:
            messages.error(request, "You do not have access to this family's details.")
            return redirect('owner_dashboard')
    else:
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    # Pending family invites (both sent by this family and requests to join this family)
    # Only for actual family parents (owners get read-only contact view)
    if profile.role == "parent":
        pending_invites = Invite.objects.filter(
            family=family,
            status="pending"
        ).select_related('sender', 'receiver').order_by('-created_at')
    else:
        pending_invites = []

    context = {
        "family": family,
        "pending_invites": pending_invites,
        "is_owner_view": profile.role == "owner",
    }
    if profile.role == "parent":
        context["kids"] = family.kids.all().order_by('first_name', 'last_name')
        context["parents"] = family.parents.all().order_by('first_name', 'last_name')

    # For owner view from roster/players: specialize to ONLY kids registered in THIS org's teams
    # and parent cards that include their TeamMembership in the org.
    # This fulfills: clickable (from players) shows only relevant org kids + parent card with membership.
    if profile.role == "owner":
        organization = Organization.objects.filter(owner=request.user).first()
        context['organization'] = organization  # always set for owner (may be None, template guards)
        if organization:
            # Registrations for this family's kids in the org's teams
            org_family_regs = list(PlayerRegistration.objects.filter(
                kid__family=family,
                team_membership__team__organization=organization
            ).select_related('kid', 'team_membership__team', 'kid__parent').order_by('kid__first_name'))

            # Parents that have at least one kid in the above regs (or all family parents, but we'll filter display)
            # Build list of (parent_user, [memberships in org])
            parents_with_memberships = []
            seen_parents = set()
            for reg in org_family_regs:
                p = reg.kid.parent
                if p.id not in seen_parents:
                    seen_parents.add(p.id)
                    mems = list(TeamMembership.objects.filter(
                        user=p,
                        team__organization=organization
                    ).select_related('team'))
                    parents_with_memberships.append((p, mems))

            # If clicked a specific parent name from roster (e.g. ?parent=123), filter to only that parent and their kids in org
            focus_parent_id = request.GET.get('parent')
            if focus_parent_id:
                try:
                    focus_parent_id = int(focus_parent_id)
                    parents_with_memberships = [(p, m) for p, m in parents_with_memberships if p.id == focus_parent_id]
                    org_family_regs = [r for r in org_family_regs if getattr(r.kid, 'parent_id', None) == focus_parent_id]
                    context['focus_parent_id'] = focus_parent_id
                except (ValueError, TypeError):
                    pass

            context.update({
                'org_family_regs': org_family_regs,
                'parents_with_memberships': parents_with_memberships,
            })

    return render(request, "core/family_details.html", context)


@login_required
def event_detail(request, event_id):
    """Detail page for a personal/family event."""
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This page is for parents only.")
        return redirect('owner_dashboard')

    user_family = profile.family
    # Scope lookup (hardened for security: no data leakage on guessed event_id)
    try:
        event = Event.objects.get(id=event_id, family=user_family)
    except Event.DoesNotExist:
        messages.error(request, "Event not found.")
        return redirect('event_list')

    context = {
        "event": event,
        "is_personal": True,
    }
    return render(request, "core/event_detail.html", context)


@login_required
def team_event_detail(request, event_id):
    """Detail page for a team event."""
    profile = _safe_get_user_profile(request)
    user_family = profile.family

    try:
        team_event = TeamEvent.objects.select_related(
            'team', 'team__organization', 'created_by'
        ).get(id=event_id)
    except TeamEvent.DoesNotExist:
        messages.error(request, "Team event not found.")
        return redirect('event_list')

    is_owner = _user_owns_team_event(team_event, request.user)
    has_access = is_owner
    if not has_access and user_family:
        has_access = TeamEventAttendance.objects.filter(
            team_event=team_event,
            kid__family=user_family,
            status='accepted'
        ).exists()

    if not has_access:
        messages.error(request, "You do not have access to this team event.")
        return redirect('event_list')
    if is_owner:
        attendances = team_event.attendances.select_related('kid').all()
    else:
        # Parents only ever see their own kids' attendance status here (no cross-family leak)
        attendances = TeamEventAttendance.objects.filter(
            team_event=team_event,
            kid__family=user_family,
            status='accepted'
        ).select_related('kid')

    summary = get_attendance_summary(team_event) if is_owner else None

    context = {
        "team_event": team_event,
        "attendances": attendances,
        "attendance_summary": summary,
        "is_team_event": True,
        "is_owner": is_owner,
    }
    return render(request, "core/team_event_detail.html", context)


@login_required
def team_detail(request, team_id):
    """Detail page for a team."""
    profile = _safe_get_user_profile(request)
    # Owners can look up any of their org's teams; members/parents look up teams they belong to.
    # We still fetch then authorize to support cross-role (owner viewing their team).
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

    players_qs = PlayerRegistration.objects.filter(
        team_membership__team=team
    ).select_related('kid', 'team_membership__user').order_by(
        'kid__first_name', 'kid__last_name', 'kid__id'
    )
    total_roster_count = players_qs.count()

    search_q = (request.GET.get('q') or '').strip()
    if search_q:
        players_qs = players_qs.filter(
            Q(kid__first_name__icontains=search_q) |
            Q(kid__last_name__icontains=search_q) |
            Q(team_membership__user__first_name__icontains=search_q) |
            Q(team_membership__user__last_name__icontains=search_q) |
            Q(team_membership__user__username__icontains=search_q)
        )

    page_obj = Paginator(players_qs, ROSTER_PLAYERS_PER_PAGE).get_page(request.GET.get('page'))

    context = {
        "team": team,
        "page_obj": page_obj,
        "total_roster_count": total_roster_count,
        "search_q": search_q,
        "is_owner": is_owner,
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
            if team_event_id:
                try:
                    te = TeamEvent.objects.select_related('team__organization').get(id=team_event_id)
                    if te.team.organization.owner_id == user.id:
                        # Owner of the team: go to details page, not the parent review page
                        base_url = reverse('team_event_detail', args=[team_event_id])
                    else:
                        base_url = reverse('review_team_event_update', args=[team_event_id])
                except TeamEvent.DoesNotExist:
                    base_url = reverse('event_list')
            else:
                base_url = reverse('event_list')
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

        item = {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "created_at": n.created_at,
            "is_read": n.is_read,
            "type": ntype,
            "url": final_url,
        }
        if ntype == "team_event_invitation":
            item["invitation_id"] = extra.get("invitation_id")
        items.append(item)

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
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

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

            # Assign super distinct team color (vibrant, not shades of same)
            existing_team_colors = set(Team.objects.filter(organization=organization).values_list('color', flat=True))
            palette = ['#64748b', '#6b7280', '#78716c', '#57534e', '#4b5563', '#9f1239', '#166534', '#1e40af', '#4338ca', '#6b21a8', '#854d0e', '#065f46', '#0f766e', '#1d4ed8', '#5b21b6']
            for c in palette:
                if c not in existing_team_colors:
                    team.color = c
                    break
            else:
                team.color = '#64748b'
            team.save()

            # TeamMembership acts as a join table between User and Team.
            # It defines the relationship + role (admin, parent, etc.) 
            # and allows one user to be part of multiple teams.
            membership = TeamMembership(team=team, user=request.user, role='admin') 
            membership.save()
            messages.success(request, f"Team '{team.name}' created successfully!")
            return redirect('team_list')
    else:
        form = TeamForm()

    return render(request, "core/create_team.html", {"form":form})


@login_required
@login_required
def team_list(request): 
    # Support ?read= for marking notifications when linked
    response = _mark_notification_as_read(request)
    if response:
        return response

    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    teams_qs = Team.objects.filter(
        organization__owner=request.user
    ).annotate(
        roster_count=Count('memberships__players', distinct=True),
    ).order_by('name')

    # Backfill distinct colors for older teams (run once per load is cheap)
    for team in teams_qs:
        if team.color in (None, '', '#3b82f6'):
            used = set(
                Team.objects.filter(organization=team.organization)
                .exclude(id=team.id)
                .values_list('color', flat=True)
            )
            palette = [
                '#64748b', '#6b7280', '#78716c', '#57534e', '#4b5563', '#9f1239',
                '#166534', '#1e40af', '#4338ca', '#6b21a8', '#854d0e', '#065f46',
                '#0f766e', '#1d4ed8', '#5b21b6',
            ]
            for c in palette:
                if c not in used:
                    team.color = c
                    team.save(update_fields=['color'])
                    break

    total_teams_count = teams_qs.count()

    search_q = (request.GET.get('q') or '').strip()
    if search_q:
        teams_qs = teams_qs.filter(
            Q(name__icontains=search_q) | Q(sport_type__icontains=search_q)
        )

    page_obj = Paginator(teams_qs, TEAMS_PER_PAGE).get_page(request.GET.get('page'))

    # ← Pass this flag that lets teamlist know its present in order to send a parent invite. user has to select team first
    from_invite = request.GET.get('from') == 'invite_parent'

    context = {
        'page_obj': page_obj,
        'total_teams_count': total_teams_count,
        'search_q': search_q,
        'from_invite': from_invite,
    }

    return render(request, "core/team_list.html", context)


@login_required
def owner_remove_kid_from_team(request, team_id, kid_id):
    """Owner removes a kid from one of their team's roster."""
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    try:
        team = Team.objects.get(id=team_id, organization__owner=request.user)
        kid = Kid.objects.get(id=kid_id)
        # Find the membership for the kid's parent on this team
        membership = TeamMembership.objects.get(team=team, user=kid.parent, role='parent')
        deleted, _ = PlayerRegistration.objects.filter(
            team_membership=membership, kid=kid
        ).delete()
        if deleted:
            messages.success(request, f"{kid.first_name} has been removed from the roster of {team.name}.")
            # Notify the parent (data leakage safe: only notify the actual kid.parent)
            parent_user = kid.parent
            if parent_user:
                Notification.objects.create(
                    user=parent_user,
                    title="Removed from Roster",
                    message=f"Your kid {kid.first_name} {kid.last_name} was removed from {team.name} by the team owner.",
                    notification_type='roster_request',
                    extra_data={'team_id': team.id, 'kid_id': kid.id, 'action': 'removed_by_owner'}
                )
            # If no more registrations for this parent on the team, remove membership
            # (prevents dangling TeamMembership with zero kids after owner removal)
            if not PlayerRegistration.objects.filter(team_membership=membership).exists():
                membership.delete()
        else:
            messages.info(request, "That kid was not on the roster.")
    except (Team.DoesNotExist, Kid.DoesNotExist, TeamMembership.DoesNotExist):
        messages.error(request, "Team, kid, or registration not found.")
    return redirect('team_list')


@login_required
def edit_team(request, team_id):
    """Owner edits one of their teams (name, sport, description)."""
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    try:
        team = Team.objects.get(id=team_id, organization__owner=request.user)
    except Team.DoesNotExist:
        messages.error(request, "Team not found.")
        return redirect('team_list')

    if request.method == "POST":
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, f"Team '{team.name}' updated successfully!")
            return redirect('team_detail', team_id=team.id)
    else:
        form = TeamForm(instance=team)

    return render(request, "core/edit_team.html", {"form": form, "team": team})


@login_required
def delete_team(request, team_id):
    """Owner deletes a team (cascades related memberships/registrations/events via FK)."""
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    try:
        team = Team.objects.get(id=team_id, organization__owner=request.user)
    except Team.DoesNotExist:
        messages.error(request, "Team not found.")
        return redirect('team_list')

    if request.method == "POST":
        team_name = team.name
        team.delete()
        messages.success(request, f"Team '{team_name}' and its data have been deleted.")
        return redirect('team_list')

    return render(request, "core/delete_team.html", {"team": team})


def _annotate_find_team_cards(parent_user, teams):
    """Attach membership / request state used by the find-teams discovery cards."""
    for team in teams:
        team.user_is_member = TeamMembership.objects.filter(
            team=team,
            user=parent_user,
        ).exists()
        team.user_has_pending_request = Invite.objects.filter(
            team=team,
            sender=parent_user,
            status='pending',
            invite_type='team_join_request',
        ).exists()
        team.can_add_more_kids = get_unregistered_kids_for_team(parent_user, team).exists()
        team.is_owned_by_user = team.organization.owner_id == parent_user.id


@login_required
def find_teams(request):
    profile = _safe_get_user_profile(request)
    if profile.role != 'parent':
        messages.error(request, 'This page is for parents only.')
        return redirect('owner_dashboard')

    search_q = (request.GET.get('q') or '').strip()
    teams_qs = Team.objects.none()
    total_results = 0
    page_obj = None

    if len(search_q) >= FIND_TEAMS_MIN_QUERY_LEN:
        search_term = search_q.casefold()
        teams_qs = Team.objects.annotate(
            _name_lower=Lower('name'),
            _org_lower=Lower('organization__name'),
        ).filter(
            Q(_name_lower__contains=search_term) | Q(_org_lower__contains=search_term)
        ).exclude(
            organization__owner=request.user,
        ).select_related('organization').order_by('name')
        total_results = teams_qs.count()
        paginator = Paginator(teams_qs, FIND_TEAMS_PER_PAGE)
        page_obj = paginator.get_page(request.GET.get('page'))
        _annotate_find_team_cards(request.user, page_obj.object_list)

    pending_requests = Invite.objects.filter(
        sender=request.user,
        status='pending',
        invite_type='team_join_request',
    ).select_related('team', 'team__organization').order_by('-created_at')

    connected_teams_count = Team.objects.filter(
        memberships__user=request.user,
        memberships__role='parent',
    ).distinct().count()

    context = {
        'search_q': search_q,
        'page_obj': page_obj,
        'teams': page_obj.object_list if page_obj else [],
        'total_results': total_results,
        'pending_requests': pending_requests,
        'connected_teams_count': connected_teams_count,
        'min_query_len': FIND_TEAMS_MIN_QUERY_LEN,
    }
    template = (
        'core/find_teams_results.html'
        if request.headers.get('HX-Request')
        else 'core/find_teams.html'
    )
    return render(request, template, context)


@login_required
@login_required
def team_to_parent_invite_search(request, team_id):
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    # Scope to this owner's teams only (prevents parents guessing team_id + correct error handling)
    try:
        team = Team.objects.get(id=team_id, organization__owner=request.user)
    except Team.DoesNotExist:
        messages.error(request, "Team not found.")
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
def select_kids_for_team_roster(request, invite_id):
    profile = _safe_get_user_profile(request)
    if profile.role != "parent":
        messages.error(request, "This is a parent feature for joining teams with kids.")
        return redirect('owner_dashboard')

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
        user_family = _safe_get_user_family(request)

        if not selected_kid_ids:
            messages.error(request, "Please select at least one kid.")
        else:
            for kid_id in selected_kid_ids:
                try:
                    kid = Kid.objects.get(id=kid_id, family=user_family)
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

    profile = _safe_get_user_profile(request)
    user_family = profile.family
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

    profile = _safe_get_user_profile(request)
    user_family = profile.family

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
        action = request.POST.get('action')
        selected_kid_ids = request.POST.getlist('kids')

        if action == 'decline':
            invitation.status = 'declined'
            invitation.save()
            # Clean any pending session
            request.session.pop('team_event_original_selection', None)
            request.session.pop('team_event_pending_kids', None)
            request.session.pop('selected_kid_ids', None)
            messages.info(request, "You have declined this team event.")
            return redirect('event_list')

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

        if auto_accepted_count > 0:
            # Notify the owner about new attendances
            owner = team_event.team.organization.owner
            if owner and owner != request.user:
                Notification.objects.get_or_create(
                    user=owner,
                    notification_type='team_event_updated',
                    extra_data__team_event_id=team_event.id,
                    defaults={
                        'title': f"New Attendance: {team_event.name}",
                        'message': f"{auto_accepted_count} kid(s) have been added to your team event '{team_event.name}'.",
                        'extra_data': {'team_event_id': team_event.id}
                    }
                )

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

    profile = _safe_get_user_profile(request)
    user_family = profile.family

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

    # Use id sets for robustness (only confirmed conflicts now)
    conflicting_ids = {k.id for k in conflicts.keys()}

    # Prepare a list for the template so we have easy per-kid conflict lists
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

    profile = _safe_get_user_profile(request)
    user_family = profile.family
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

    # Notify the owner about the new attendance
    owner = team_event.team.organization.owner
    if owner and owner != request.user:
        kid_full_name = f"{chosen_kid.first_name} {chosen_kid.last_name}".strip()
        Notification.objects.get_or_create(
            user=owner,
            notification_type='team_event_updated',
            extra_data__team_event_id=team_event.id,
            defaults={
                'title': f"New Attendance: {team_event.name}",
                'message': f"{kid_full_name} has been added to your team event '{team_event.name}'.",
                'extra_data': {'team_event_id': team_event.id, 'kid_id': chosen_kid.id}
            }
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
@login_required
def create_organization(request):
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    if Organization.objects.filter(owner=request.user).exists():
        messages.error(request, "You already have an organization.")
        return redirect('owner_dashboard')
    if request.method == "POST":
        form = OrganizationForm(request.POST)
        if form.is_valid():
            organization = form.save(commit=False)
            organization.owner = request.user          # Important: Set current user as owner
            organization.save()
            
            messages.success(request, f"Organization '{organization.name}' created successfully! Now create your first team.")
            return redirect('team_list')
    else:
        form = OrganizationForm()

    return render(request, "core/create_organization.html", {"form": form})

@login_required
def organization_details(request, org_id):
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    try:
        organization = Organization.objects.get(id=org_id, owner=request.user)
    except Organization.DoesNotExist:
        messages.error(request, "Organization not found.")
        return redirect('owner_dashboard')

    context = {
        'organization': organization,
    }
    return render(request, 'core/organization_details.html', context)


@login_required
def edit_organization(request, org_id):
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    try:
        organization = Organization.objects.get(id=org_id, owner=request.user)
    except Organization.DoesNotExist:
        messages.error(request, "Organization not found.")
        return redirect('owner_dashboard')

    if request.method == "POST":
        form = OrganizationForm(request.POST, instance=organization)
        if form.is_valid():
            form.save()
            messages.success(request, f"Organization '{organization.name}' updated successfully!")
            return redirect('organization_details', org_id=organization.id)
    else:
        form = OrganizationForm(instance=organization)

    return render(request, "core/edit_organization.html", {
        "form": form,
        "organization": organization,
    })


@login_required
def my_organization(request):
    """Convenience view for owners to see their own organization (used in nav)."""
    profile = _safe_get_user_profile(request)
    if profile.role != "owner":
        messages.error(request, "This page is for owners only.")
        return redirect('dashboard')

    try:
        organization = Organization.objects.get(owner=request.user)
    except Organization.DoesNotExist:
        messages.error(request, "You don't have an organization yet.")
        return redirect('create_organization')

    # Delegate to details view logic for consistent stats
    return organization_details(request, organization.id)


# =====================================================================================
# STEP 3: PRIVACY, DATA PROTECTION & ACCOUNT DELETION / EXPORT
# =====================================================================================

def _collect_user_data_for_export(user):
    """
    Gather a safe, complete snapshot of the user's own data for export (GDPR Art. 20 style).
    Never includes other users' data. Used by export_data view.
    """
    profile = getattr(user, 'profile', None)
    data = {
        "exported_at": timezone.now().isoformat(),
        "user": {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "date_joined": user.date_joined.isoformat() if user.date_joined else None,
        },
        "profile": None,
        "role": profile.role if profile else None,
        "kids": [],
        "personal_events": [],
        "team_memberships": [],
        "managed_organizations": [],
        "notifications": [],
        "invites_sent": [],
        "invites_received": [],
    }

    if profile:
        data["profile"] = {
            "timezone": profile.timezone,
            "phone": profile.phone,
            "data_consent_at": profile.data_consent_at.isoformat() if profile.data_consent_at else None,
            "family_id": profile.family_id,
        }

    # Kids (only this user's; parent FK)
    for kid in Kid.objects.filter(parent=user).select_related('family'):
        data["kids"].append({
            "id": kid.id,
            "first_name": kid.first_name,
            "last_name": kid.last_name,
            "date_of_birth": kid.date_of_birth.isoformat(),
            "gender": kid.gender,
            "color": kid.color,
            "family_name": kid.family.family_name if kid.family else None,
            "created_at": kid.created_at.isoformat(),
        })

    # Personal / Family Events created by user or where user is attending (scoped to their data)
    # Include events from families the user belonged to (via profile at time, but we use created_by + attending + kids owned)
    user_kid_ids = list(Kid.objects.filter(parent=user).values_list('id', flat=True))
    events_qs = Event.objects.filter(
        Q(created_by=user) |
        Q(attending_parents=user) |
        Q(kids__id__in=user_kid_ids)
    ).distinct().select_related('family').prefetch_related('kids', 'attending_parents')

    for ev in events_qs:
        data["personal_events"].append({
            "id": ev.id,
            "name": ev.name,
            "start_time": ev.start_time.isoformat(),
            "end_time": ev.end_time.isoformat(),
            "location": ev.location,
            "description": ev.description,
            "family_name": ev.family.family_name if ev.family else None,
            "kids": [f"{k.first_name} {k.last_name}" for k in ev.kids.all()],
            "attending_parents": [u.username for u in ev.attending_parents.all()],
            "created_by_id": ev.created_by_id,
            "created_at": ev.created_at.isoformat(),
        })

    # Team memberships (as parent or admin)
    for tm in TeamMembership.objects.filter(user=user).select_related('team', 'team__organization'):
        data["team_memberships"].append({
            "team_id": tm.team_id,
            "team_name": tm.team.name,
            "organization": tm.team.organization.name,
            "role": tm.role,
            "jersey_number": tm.jersey_number,
            "joined_at": tm.joined_at.isoformat(),
        })

    # Organizations + teams they own (full management data snapshot)
    for org in Organization.objects.filter(owner=user).prefetch_related('teams'):
        org_data = {
            "id": org.id,
            "name": org.name,
            "description": org.description,
            "created_at": org.created_at.isoformat(),
            "teams": [],
        }
        for team in org.teams.all():
            team_data = {
                "id": team.id,
                "name": team.name,
                "sport_type": team.sport_type,
                "description": team.description,
                "members": [],
            }
            for mem in TeamMembership.objects.filter(team=team).select_related('user'):
                team_data["members"].append({
                    "user_id": mem.user_id,
                    "username": mem.user.username,
                    "role": mem.role,
                })
            org_data["teams"].append(team_data)
        data["managed_organizations"].append(org_data)

    # Recent notifications (their own)
    for n in Notification.objects.filter(user=user)[:100]:
        data["notifications"].append({
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "type": n.notification_type,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
        })

    # Invites
    for inv in Invite.objects.filter(sender=user):
        data["invites_sent"].append({
            "id": inv.id, "type": inv.invite_type, "status": inv.status,
            "created_at": inv.created_at.isoformat()
        })
    for inv in Invite.objects.filter(receiver=user):
        data["invites_received"].append({
            "id": inv.id, "type": inv.invite_type, "status": inv.status,
            "created_at": inv.created_at.isoformat()
        })

    return data


@login_required
def export_data(request):
    """
    Allow user to download a JSON export of all data associated with their account.
    Implements "right to data portability".
    Only their data; safe for parents (kids + events) and owners (orgs + teams they manage).
    """
    user = request.user
    data = _collect_user_data_for_export(user)

    filename = f"fusion_data_export_{user.username}_{timezone.now().strftime('%Y%m%d_%H%M')}.json"

    response = HttpResponse(
        json.dumps(data, indent=2, default=str),
        content_type='application/json'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _perform_account_deletion(user, request):
    """
    Carefully delete or clean all data for a user account.
    Follows "right to be forgotten" while preserving data integrity for remaining users.
    - For owners: deletes their Organization(s) + cascades (teams, events, roster links, attendances).
      Kids of OTHER parents remain; only links and team-owned events are removed.
    - For parents: deletes their Kids (cascades attendances), cleans their events (like delete_kid),
      removes from shared families (transfers created_by), removes from teams (cascades memberships/regs).
    - All notifications, invites, team event invites for the user are removed.
    - M2M attendances cleaned.
    - Finally deletes the User (cascades Profile, remaining owner refs already handled).
    Logs to AccountDeletionLog + python logger before destructive action.
    """
    profile = getattr(user, 'profile', None)
    role = profile.role if profile else 'unknown'
    ip = request.META.get('REMOTE_ADDR') if request else None

    # 1. AUDIT LOG (before any delete)
    try:
        AccountDeletionLog.objects.create(
            user_id=user.id,
            username=user.username,
            role=role,
            ip_address=ip
        )
    except Exception:
        pass  # never block deletion on log failure

    deletion_logger.warning(
        "ACCOUNT DELETION initiated for user_id=%s username=%s role=%s ip=%s",
        user.id, user.username, role, ip
    )

    # 2. Notifications (own)
    Notification.objects.filter(user=user).delete()

    # 3. TeamEventInvitation (invites to this user)
    TeamEventInvitation.objects.filter(user=user).delete()

    # 4. Invites involving user (family/team join etc)
    Invite.objects.filter(Q(sender=user) | Q(receiver=user)).delete()

    # 5. Remove from personal event M2M attending_parents (do not delete events owned by others)
    for event in list(Event.objects.filter(attending_parents=user)):
        event.attending_parents.remove(user)

    # 6. OWNER PATH: delete owned organizations (cascades Teams, TeamEvents, TeamEventAttendance,
    #    TeamMembership, PlayerRegistration, Invites on teams, etc.)
    #    This removes roster links and team event history from the org, but does NOT delete
    #    the Kid or Family records of the parents (their kids stay in their families).
    if role == 'owner':
        for org in list(Organization.objects.filter(owner=user)):
            org_name = org.name
            org.delete()
            deletion_logger.info("Deleted owned Organization id=%s name=%s during account deletion", org.id, org_name)

    # 7. PARENT PATH / shared family handling + kid cleanup
    # Transfer family created_by if other parents exist; delete family only if sole.
    for family in list(Family.objects.filter(created_by=user)):
        other_parents = User.objects.filter(profile__family=family).exclude(id=user.id)
        if other_parents.exists():
            family.created_by = other_parents.first()
            family.save(update_fields=['created_by'])
            deletion_logger.info("Transferred family created_by for family_id=%s (user leaving but others remain)", family.id)
        else:
            # Safe: sole creator, delete family (cascades its Events + Kids via their FKs)
            family.delete()
            deletion_logger.info("Deleted sole-owned family_id=%s during account deletion", family.id)

    # 8. Clean events involving this user's kids (mirrors delete_kid logic to avoid orphan events)
    # Must do BEFORE user.delete() which will CASCADE delete the Kid rows.
    user_kids = list(Kid.objects.filter(parent=user).select_related('family'))
    for kid in user_kids:
        for event in list(kid.events.all()):
            if event.kids.count() <= 1:
                event.delete()
            else:
                event.kids.remove(kid)

    # Also delete any PlayerRegistrations for these kids (belt & suspenders; kid delete will cascade too)
    if user_kids:
        kid_ids = [k.id for k in user_kids]
        PlayerRegistration.objects.filter(kid_id__in=kid_ids).delete()

    # 9. TeamEventAttendance for user's kids will cascade on kid delete (kid FK CASCADE)
    # TeamMembership for user will cascade delete on user delete (good, removes from rosters)

    # 10. Finally delete the user.
    # - This CASCADE deletes: Profile, Kid (parent), TeamMembership (user), Notification (already done),
    #   Invite (already), TeamEventInvitation (already), and SET_NULLs Event.created_by, Family.created_by (we handled).
    # - For owners we already deleted orgs so no CASCADE org delete on user.
    deleted_username = user.username
    user.delete()

    deletion_logger.warning("ACCOUNT DELETION COMPLETED for former username=%s (user record removed)", deleted_username)

    return True


@login_required
def delete_account(request):
    """
    Safe, confirmed account deletion flow.
    - GET: shows serious warning page with exactly what will be deleted based on role.
    - POST: requires current password confirmation + CSRF.
    - On success: performs full cleanup, logs, logs user out, redirects to login with message.
    - Never deletes without explicit confirmation.
    """
    user = request.user
    profile = _safe_get_user_profile(request)
    role = profile.role

    if request.method == "POST":
        password = request.POST.get("password", "")
        confirm_text = request.POST.get("confirm_text", "").strip()

        # Double confirmation: password + typed "DELETE"
        if confirm_text != "DELETE":
            messages.error(request, "You must type DELETE exactly to confirm.")
            return redirect('delete_account')

        if not user.check_password(password):
            messages.error(request, "Incorrect password. Account was not deleted.")
            return redirect('delete_account')

        # Perform the deletion (logs + cleans + deletes user)
        try:
            _perform_account_deletion(user, request)
        except Exception as e:
            deletion_logger.exception("Deletion failed for user %s: %s", user.id, e)
            messages.error(request, "An error occurred during deletion. Please contact support.")
            # Do not log them out; they can try again or contact
            return redirect('account_settings')

        # Success: user is gone. Log out (session will be invalid anyway) and inform.
        logout(request)
        # We can't use messages after logout easily for next request; use query param or session flash alternative.
        # For simplicity, redirect to login and template can check ?deleted=1
        return redirect(reverse('login') + '?deleted=1')

    # GET - render confirmation with warnings
    # Compute summary for the template (what will be removed)
    num_kids = Kid.objects.filter(parent=user).count()
    num_personal_events = Event.objects.filter(
        Q(created_by=user) | Q(attending_parents=user) | Q(kids__parent=user)
    ).distinct().count()
    num_orgs = Organization.objects.filter(owner=user).count()
    num_teams_managed = Team.objects.filter(organization__owner=user).count()
    num_team_memberships = TeamMembership.objects.filter(user=user).count()
    num_notifications = Notification.objects.filter(user=user).count()
    num_invites = Invite.objects.filter(Q(sender=user) | Q(receiver=user)).count()

    context = {
        "role": role,
        "num_kids": num_kids,
        "num_personal_events": num_personal_events,
        "num_orgs": num_orgs,
        "num_teams_managed": num_teams_managed,
        "num_team_memberships": num_team_memberships,
        "num_notifications": num_notifications,
        "num_invites": num_invites,
        "has_family": bool(getattr(profile, 'family', None)),
    }
    return render(request, "core/delete_account.html", context)



def privacy_policy(request):
    """Public page: Privacy Policy (linked from footer, signup, login, settings)."""
    return render(request, "core/privacy_policy.html")


def terms_of_service(request):
    """Public page: Terms of Service (linked from footer, signup, login, settings)."""
    return render(request, "core/terms_of_service.html")





    
            
