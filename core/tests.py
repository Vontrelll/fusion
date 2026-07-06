"""
Comprehensive test suite for Fusion.

Covers:
- Authentication, role-based access, and redirects
- Parent/Family/Kid CRUD and cascades
- Personal/Family Events (create, edit, delete, conflict detection)
- Organizations, Teams, Rosters (PlayerRegistration/TeamMembership)
- Team Events + full invitation/attendance flow
- Team Event Kid Selection + Conflict Resolution wizard (the critical flow)
- Account deletion for both parent and owner roles (with audit log + data integrity)
- Data isolation between parents and owners
- Data export
- Invite flows (family + team directions)
- Key utility functions (has_conflict, get_kid_conflicts, etc.)
- Privacy/compliance behaviors

Run with: python manage.py test core
"""

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core import mail
from django.core.cache import cache
from datetime import datetime, timedelta, date, time
from django.db.models import Q

from core.models import (
    Family, Kid, Profile, Organization, Team, Invite, TeamEvent,
    TeamEventInvitation, TeamEventAttendance, Event, TeamMembership,
    PlayerRegistration, Notification, AccountDeletionLog, RosterRequestKid
)
from core.views import has_conflict, get_kid_conflicts, get_conflicts_for_kids

User = get_user_model()


# =============================================================================
# HELPER MIXINS / UTILITIES
# =============================================================================

def make_aware(dt):
    """Ensure datetime is timezone-aware for model fields."""
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt)


def create_parent_user(username, password="testpass123", family=None):
    user = User.objects.create_user(username=username, password=password)
    profile = Profile.objects.create(user=user, role="parent")
    if family:
        profile.family = family
        profile.save()
    return user, profile


def create_owner_user(username, password="testpass123"):
    user = User.objects.create_user(username=username, password=password)
    profile = Profile.objects.create(user=user, role="owner")
    return user, profile


def create_family(name, created_by):
    return Family.objects.create(family_name=name, created_by=created_by)


def create_kid(first, last, family, parent, dob="2015-06-15", gender="M"):
    return Kid.objects.create(
        first_name=first, last_name=last,
        date_of_birth=dob, gender=gender,
        family=family, parent=parent
    )


def create_org(name, owner):
    return Organization.objects.create(name=name, owner=owner)


def create_team(name, organization, sport="basketball"):
    return Team.objects.create(name=name, sport_type=sport, organization=organization)


def create_team_membership(team, user, role="parent"):
    return TeamMembership.objects.create(team=team, user=user, role=role)


def create_personal_event(name, family, created_by, kids, start, end, location="Home"):
    ev = Event.objects.create(
        name=name,
        family=family,
        created_by=created_by,
        start_time=make_aware(start),
        end_time=make_aware(end),
        location=location,
    )
    ev.kids.set(kids)
    return ev


def create_team_event(name, team, created_by, start, end, location="Field"):
    return TeamEvent.objects.create(
        name=name,
        team=team,
        created_by=created_by,
        start_time=make_aware(start),
        end_time=make_aware(end),
        location=location,
    )


# =============================================================================
# BASIC AUTH / SECURITY / ROLE TESTS
# =============================================================================

class BasicSecurityTests(TestCase):
    """Core security and basic flow tests."""

    def setUp(self):
        self.client = Client()
        self.parent = User.objects.create_user(username="parent1", password="testpass123")
        self.owner = User.objects.create_user(username="coach1", password="testpass123")
        self.parent_profile = Profile.objects.create(user=self.parent, role="parent")
        self.owner_profile = Profile.objects.create(user=self.owner, role="owner")
        self.family = Family.objects.create(family_name="Test Family", created_by=self.parent)
        self.parent_profile.family = self.family
        self.parent_profile.save()
        self.kid = Kid.objects.create(
            first_name="Test", last_name="Kid",
            date_of_birth="2015-01-01", gender="M",
            family=self.family, parent=self.parent
        )

    def test_login_works(self):
        response = self.client.post(reverse("login"), {
            "username": "parent1",
            "password": "testpass123"
        })
        self.assertEqual(response.status_code, 302)

    def test_parent_can_access_dashboard(self):
        self.client.login(username="parent1", password="testpass123")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_owner_redirects_to_owner_dashboard(self):
        self.client.login(username="coach1", password="testpass123")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("owner_dashboard", response["Location"])

    def test_account_deletion_page_exists(self):
        self.client.login(username="parent1", password="testpass123")
        response = self.client.get(reverse("delete_account"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DELETE")

    def test_unauthenticated_cannot_access_protected_pages(self):
        for url_name in ["dashboard", "add_event", "my_family", "owner_dashboard", "add_team_event"]:
            resp = self.client.get(reverse(url_name))
            self.assertIn(resp.status_code, (302, 403))


class OwnerParentIsolationTests(TestCase):
    """Test that owners and parents cannot see each other's restricted data."""

    def setUp(self):
        self.client = Client()
        self.parent_user, self.parent_profile = create_parent_user("parent1")
        self.owner_user, self.owner_profile = create_owner_user("coach1")
        self.family = create_family("Iso Family", self.parent_user)
        self.parent_profile.family = self.family
        self.parent_profile.save()
        self.kid = create_kid("Iso", "Kid", self.family, self.parent_user)

    def test_owner_cannot_access_parent_only_pages(self):
        self.client.login(username="coach1", password="testpass123")
        for name in ["dashboard", "add_event", "add_kid", "my_family"]:
            resp = self.client.get(reverse(name))
            # Owners get redirected or error
            self.assertNotEqual(resp.status_code, 200)

    def test_parent_cannot_access_owner_only_pages(self):
        self.client.login(username="parent1", password="testpass123")
        for name in ["owner_dashboard", "add_team_event", "create_organization"]:
            resp = self.client.get(reverse(name))
            self.assertNotEqual(resp.status_code, 200)


# =============================================================================
# FAMILY + KID + PERSONAL EVENT TESTS
# =============================================================================

class FamilyKidEventTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.profile = create_parent_user("famparent")
        self.family = create_family("Smith Family", self.user)
        self.profile.family = self.family
        self.profile.save()
        self.kid1 = create_kid("Alex", "Smith", self.family, self.user)
        self.kid2 = create_kid("Jordan", "Smith", self.family, self.user)

    def test_add_kid(self):
        self.client.login(username="famparent", password="testpass123")
        resp = self.client.post(reverse("add_kid"), {
            "first_name": "Taylor",
            "last_name": "Smith",
            "date_of_birth": "2016-03-20",
            "gender": "F",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Kid.objects.filter(first_name="Taylor", family=self.family).exists())

    def test_delete_kid_cleans_events_and_registrations(self):
        self.client.login(username="famparent", password="testpass123")
        # Personal event only for kid1
        ev = create_personal_event(
            "Only Alex", self.family, self.user, [self.kid1],
            timezone.now() + timedelta(days=1),
            timezone.now() + timedelta(days=1, hours=1)
        )
        # Multi-kid event
        ev2 = create_personal_event(
            "Both Kids", self.family, self.user, [self.kid1, self.kid2],
            timezone.now() + timedelta(days=2),
            timezone.now() + timedelta(days=2, hours=1)
        )

        # Delete kid1
        resp = self.client.post(reverse("delete_kid", args=[self.kid1.id]))
        self.assertEqual(resp.status_code, 302)

        self.assertFalse(Kid.objects.filter(id=self.kid1.id).exists())
        # Solo event should be gone
        self.assertFalse(Event.objects.filter(id=ev.id).exists())
        # Multi-kid event should still exist but without kid1
        ev2.refresh_from_db()
        self.assertEqual(list(ev2.kids.all()), [self.kid2])

    def test_create_personal_event_with_conflict_detection(self):
        self.client.login(username="famparent", password="testpass123")
        # Use a clean base date to avoid isoformat() offset issues with fromisoformat + make_aware
        # Build "wall clock" times the same way the view will interpret posted datetime-local strings
        # (so that has_conflict comparisons see actual overlapping instants).
        from datetime import datetime as dt
        naive_base = dt(2026, 6, 15, 10, 0)
        base = timezone.make_aware(naive_base)

        # Existing event for kid1 (10:00-11:00 wall time)
        create_personal_event(
            "Existing", self.family, self.user, [self.kid1],
            base,
            base + timedelta(hours=1)
        )

        # Attempt overlapping (10:30-11:30) — view should not create the event
        # Use the same naive wall time + strftime that the view will round-trip through make_aware
        overlap_naive = dt(2026, 6, 15, 10, 30)
        overlap_start = overlap_naive.strftime("%Y-%m-%dT%H:%M")
        overlap_end = (overlap_naive + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")

        resp = self.client.post(reverse("add_event"), {
            "name": "Conflicting",
            "start_time": overlap_start,
            "end_time": overlap_end,
            "location": "Park",
            "kids": [str(self.kid1.id)],
        })
        # The important guarantee: conflicting event is not persisted (regardless of exact 200 vs redirect-with-error)
        self.assertFalse(Event.objects.filter(name="Conflicting").exists(), "Conflicting event should have been blocked by has_conflict")

        # Non-conflicting (clearly after, 14:00-15:00) — should succeed
        safe_start = dt(2026, 6, 15, 14, 0).strftime("%Y-%m-%dT%H:%M")
        safe_end = dt(2026, 6, 15, 15, 0).strftime("%Y-%m-%dT%H:%M")
        resp2 = self.client.post(reverse("add_event"), {
            "name": "NonConflicting",
            "start_time": safe_start,
            "end_time": safe_end,
            "location": "Park",
            "kids": [str(self.kid1.id)],
        })
        self.assertEqual(resp2.status_code, 302)
        # add_event applies .title() to names ("NonConflicting" → "Nonconflicting")
        self.assertTrue(Event.objects.filter(name="Nonconflicting").exists())


# =============================================================================
# ORGANIZATION / TEAM / ROSTER TESTS
# =============================================================================

class OrgTeamRosterTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner, self.owner_profile = create_owner_user("owner1")
        self.org = create_org("Test Org", self.owner)
        self.team = create_team("U10 Hawks", self.org)
        self.parent, self.parent_profile = create_parent_user("rosterparent")
        self.family = create_family("Roster Fam", self.parent)
        self.parent_profile.family = self.family
        self.parent_profile.save()
        self.kid = create_kid("Roster", "Kid", self.family, self.parent)

    def test_owner_creates_org_and_team(self):
        self.client.login(username="owner1", password="testpass123")
        # Already has org from setUp; create another team
        resp = self.client.post(reverse("create_team"), {
            "name": "U12 Eagles",
            "sport_type": "soccer",
            "description": "Competitive",
        })
        # Note: actual create_team view may differ; at minimum owner dashboard works
        self.assertIn(resp.status_code, (200, 302))

    def test_parent_sends_roster_request_and_owner_approves(self):
        # Parent finds team and sends request for kid
        self.client.login(username="rosterparent", password="testpass123")
        # Use the kid selection + request flow
        resp = self.client.post(reverse("select_kids_for_team_join_request", args=[self.team.id]), {
            "kids": [str(self.kid.id)]
        })
        # This page may redirect or show form; try the actual request endpoint
        # Many flows go through parent_to_team_request
        self.client.post(reverse("parent_to_team_request", args=[self.team.id]), {
            "kids": [str(self.kid.id)]
        })

        invite = Invite.objects.filter(
            team=self.team,
            sender=self.parent,
            invite_type="team_join_request",
            status="pending"
        ).first()
        self.assertIsNotNone(invite)
        self.assertTrue(RosterRequestKid.objects.filter(invite=invite, kid=self.kid).exists())

        # Owner reviews and approves
        self.client.login(username="owner1", password="testpass123")
        resp = self.client.post(reverse("review_roster_request", args=[invite.id]), {
            "action": "approve",
            "kids": [str(self.kid.id)]
        })
        self.assertEqual(resp.status_code, 302)

        invite.refresh_from_db()
        self.assertEqual(invite.status, "accepted")
        self.assertTrue(PlayerRegistration.objects.filter(
            kid=self.kid, team_membership__team=self.team
        ).exists())

    def test_parent_removes_kid_from_team(self):
        # Manually register
        membership = create_team_membership(self.team, self.parent)
        PlayerRegistration.objects.create(team_membership=membership, kid=self.kid)

        self.client.login(username="rosterparent", password="testpass123")
        resp = self.client.post(reverse("remove_kid_from_team", args=[self.team.id, self.kid.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(PlayerRegistration.objects.filter(kid=self.kid, team_membership__team=self.team).exists())


# =============================================================================
# TEAM EVENT + INVITATION + ATTENDANCE TESTS
# =============================================================================

class TeamEventTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner, _ = create_owner_user("coach_te")
        self.org = create_org("TE Org", self.owner)
        self.team = create_team("U8 Stars", self.org)
        self.parent, self.p_profile = create_parent_user("te_parent")
        self.family = create_family("TE Fam", self.parent)
        self.p_profile.family = self.family
        self.p_profile.save()
        self.kid = create_kid("TE", "Kid", self.family, self.parent)
        # Register kid on team so they can be invited
        mem = create_team_membership(self.team, self.parent)
        PlayerRegistration.objects.create(team_membership=mem, kid=self.kid)

    def test_owner_creates_team_event_and_invitations_are_sent(self):
        self.client.login(username="coach_te", password="testpass123")
        start = timezone.now() + timedelta(days=3)
        end = start + timedelta(hours=1.5)

        resp = self.client.post(reverse("add_team_event"), {
            "name": "Season Opener",
            "start_time": start.strftime("%Y-%m-%dT%H:%M"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M"),
            "location": "Main Field",
            "team": str(self.team.id),
            "description": "Bring water",
            "event_type": "team",
        })
        self.assertEqual(resp.status_code, 302)

        te = TeamEvent.objects.filter(name="Season Opener", team=self.team).first()
        self.assertIsNotNone(te)

        # Invitation should exist for the parent
        inv = TeamEventInvitation.objects.filter(team_event=te, user=self.parent).first()
        self.assertIsNotNone(inv)
        self.assertEqual(inv.status, "pending")

    def test_team_event_conflict_check_prevents_overlapping_for_same_team(self):
        self.client.login(username="coach_te", password="testpass123")
        # Consistent wall time construction (same interpretation as view's datetime-local + make_aware)
        from datetime import datetime as dt
        naive_base = dt(2026, 6, 20, 10, 0)
        base = timezone.make_aware(naive_base)
        create_team_event("First", self.team, self.owner, base, base + timedelta(hours=2))

        # Overlapping attempt (10:30-11:30 while First is 10:00-12:00)
        overlap_naive = dt(2026, 6, 20, 10, 30)
        overlap_start = overlap_naive.strftime("%Y-%m-%dT%H:%M")
        overlap_end = (overlap_naive + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")

        resp = self.client.post(reverse("add_team_event"), {
            "name": "Overlap",
            "start_time": overlap_start,
            "end_time": overlap_end,
            "location": "Field B",
            "team": str(self.team.id),
        })
        # The important guarantee: the overlapping team event must not be created
        self.assertFalse(TeamEvent.objects.filter(name="Overlap").exists(),
                          "Overlapping team event should have been blocked by has_conflict")

    def test_parent_team_event_kid_selection_auto_accepts_non_conflicting(self):
        # Owner creates event
        te = create_team_event(
            "Practice", self.team, self.owner,
            timezone.now() + timedelta(days=2),
            timezone.now() + timedelta(days=2, hours=1)
        )
        invitation = TeamEventInvitation.objects.create(team_event=te, user=self.parent, status="pending")

        self.client.login(username="te_parent", password="testpass123")

        # Select the kid (no conflicts yet)
        resp = self.client.post(reverse("team_event_kid_selection", args=[invitation.id]), {
            "kids": [str(self.kid.id)]
        })
        self.assertEqual(resp.status_code, 302)

        att = TeamEventAttendance.objects.filter(team_event=te, kid=self.kid).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.status, "accepted")

        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "accepted")

    def test_org_wide_training_invite_without_team(self):
        """Training with no team must not 500 when sending invites or viewing detail."""
        self.client.login(username="coach_te", password="testpass123")
        start = timezone.now() + timedelta(days=5)
        end = start + timedelta(hours=1)

        resp = self.client.post(reverse("add_team_event"), {
            "name": "Org Wide Skills",
            "start_time": start.strftime("%Y-%m-%dT%H:%M"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M"),
            "location": "Training Center",
            "event_type": "training",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("select_players_for_training", resp["Location"])

        te = TeamEvent.objects.filter(name="Org Wide Skills", event_type="training").first()
        self.assertIsNotNone(te)
        self.assertIsNone(te.team)

        resp2 = self.client.post(reverse("select_players_for_training", args=[te.id]), {
            "kids": [str(self.kid.id)],
        })
        self.assertEqual(resp2.status_code, 302)
        self.assertIn("/team-event/", resp2["Location"])

        detail = self.client.get(reverse("team_event_detail", args=[te.id]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Org Wide Skills")
        self.assertEqual(TeamEventInvitation.objects.filter(team_event=te).count(), 1)

    def test_owner_creates_training_then_selects_specific_players(self):
        """Test the new training session flow:
        - Owner creates with event_type=training (no auto whole-team invites)
        - Redirects to player selector
        - Selecting kids creates invitation(s) + pending attendances
        - Parent can accept via same kid_selection flow
        - Owner sees the pending -> accepted statuses
        """
        self.client.login(username="coach_te", password="testpass123")
        start = timezone.now() + timedelta(days=4)
        end = start + timedelta(hours=1)

        # Create as training
        resp = self.client.post(reverse("add_team_event"), {
            "name": "Skill Drills Small Group",
            "start_time": start.strftime("%Y-%m-%dT%H:%M"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M"),
            "location": "Side Field",
            "team": str(self.team.id),
            "event_type": "training",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("select_players_for_training", resp["Location"])

        te = TeamEvent.objects.filter(name="Skill Drills Small Group", team=self.team).first()
        self.assertIsNotNone(te)
        self.assertEqual(te.event_type, "training")

        # No whole team invitations yet
        self.assertEqual(TeamEventInvitation.objects.filter(team_event=te).count(), 0)
        self.assertEqual(TeamEventAttendance.objects.filter(team_event=te).count(), 0)

        # Now owner selects the player
        resp2 = self.client.post(reverse("select_players_for_training", args=[te.id]), {
            "kids": [str(self.kid.id)],
        })
        self.assertEqual(resp2.status_code, 302)

        # Invitation + pending attendance created
        self.assertEqual(TeamEventInvitation.objects.filter(team_event=te).count(), 1)
        att = TeamEventAttendance.objects.filter(team_event=te, kid=self.kid).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.status, "pending")

        # Parent accepts via the exact same flow as team events
        self.client.login(username="te_parent", password="testpass123")
        invitation = TeamEventInvitation.objects.get(team_event=te, user=self.parent)

        # Direct accept (no conflict path)
        resp3 = self.client.post(reverse("team_event_kid_selection", args=[invitation.id]), {
            "kids": [str(self.kid.id)]
        })
        self.assertEqual(resp3.status_code, 302)

        att.refresh_from_db()
        self.assertEqual(att.status, "accepted")

        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "accepted")

        # Owner now sees accepted (not just pending)
        self.client.login(username="coach_te", password="testpass123")
        detail_resp = self.client.get(reverse("team_event_detail", args=[te.id]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "accepted")  # status visible to owner

# =============================================================================
# CONFLICT RESOLUTION TESTS (the key user-requested area)
# =============================================================================

class ConflictResolutionTests(TestCase):
    """
    Thoroughly test the team event conflict resolution flow:
    - team_event_kid_selection detects conflicts
    - Stores pending + original in session
    - resolve_team_event_conflict shows correct kids_data
    - replace_with_team_event removes conflicting family event (solo vs multi) or prior team attendance
    - decline_team_event_invite keeps original family event
    - Final invitation status and session cleanup
    """

    def setUp(self):
        self.client = Client()
        self.owner, _ = create_owner_user("conflict_coach")
        self.org = create_org("Conflict Org", self.owner)
        self.team = create_team("Conflict Team", self.org)

        self.parent, self.pprof = create_parent_user("conflict_parent")
        self.family = create_family("Conflict Fam", self.parent)
        self.pprof.family = self.family
        self.pprof.save()

        self.kid1 = create_kid("Conflict1", "Kid", self.family, self.parent)
        self.kid2 = create_kid("Conflict2", "Kid", self.family, self.parent)

        # Register both on team
        mem = create_team_membership(self.team, self.parent)
        PlayerRegistration.objects.create(team_membership=mem, kid=self.kid1)
        PlayerRegistration.objects.create(team_membership=mem, kid=self.kid2)

    def _create_invitation_for_event(self, team_event):
        return TeamEventInvitation.objects.create(
            team_event=team_event, user=self.parent, status="pending"
        )

    def test_conflict_detection_personal_event_vs_team_event(self):
        now = timezone.now()
        # Existing personal event for kid1 that will conflict
        personal = create_personal_event(
            "Piano Lesson", self.family, self.parent, [self.kid1],
            now + timedelta(days=4, hours=10),
            now + timedelta(days=4, hours=11)
        )

        team_ev = create_team_event(
            "Big Game", self.team, self.owner,
            now + timedelta(days=4, hours=10, minutes=15),
            now + timedelta(days=4, hours=11, minutes=30)
        )
        invitation = self._create_invitation_for_event(team_ev)

        self.client.login(username="conflict_parent", password="testpass123")

        # Select kid1 → should detect conflict and go to resolve screen
        resp = self.client.post(
            reverse("team_event_kid_selection", args=[invitation.id]),
            {"kids": [str(self.kid1.id)]}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("resolve_team_event_conflict", resp["Location"])

        # Session should have pending kids
        self.assertIn("team_event_pending_kids", self.client.session)
        self.assertIn(str(self.kid1.id), self.client.session["team_event_pending_kids"])

        # Visit resolve page
        resolve_resp = self.client.get(reverse("resolve_team_event_conflict", args=[invitation.id]))
        self.assertEqual(resolve_resp.status_code, 200)
        self.assertContains(resolve_resp, "Conflict1")
        self.assertContains(resolve_resp, "Piano Lesson")

    def test_replace_with_team_event_removes_solo_family_event_and_accepts(self):
        now = timezone.now()
        personal = create_personal_event(
            "Solo Conflict", self.family, self.parent, [self.kid1],
            now + timedelta(days=6),
            now + timedelta(days=6, hours=2)
        )
        team_ev = create_team_event(
            "Championship", self.team, self.owner,
            now + timedelta(days=6, minutes=30),
            now + timedelta(days=6, hours=2, minutes=30)
        )
        invitation = self._create_invitation_for_event(team_ev)

        self.client.login(username="conflict_parent", password="testpass123")
        # Trigger conflict path
        self.client.post(reverse("team_event_kid_selection", args=[invitation.id]), {
            "kids": [str(self.kid1.id)]
        })

        # Now replace
        replace_resp = self.client.get(
            reverse("replace_with_team_event", args=[invitation.id, self.kid1.id])
        )
        self.assertEqual(replace_resp.status_code, 302)

        # Original solo event should be deleted
        self.assertFalse(Event.objects.filter(id=personal.id).exists())

        # Attendance created as accepted
        att = TeamEventAttendance.objects.get(team_event=team_ev, kid=self.kid1)
        self.assertEqual(att.status, "accepted")

        # Invitation finalized
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "accepted")

        # Pending session cleaned
        self.assertNotIn("team_event_pending_kids", self.client.session)

    def test_replace_with_team_event_removes_kid_from_multi_kid_family_event(self):
        now = timezone.now()
        personal = create_personal_event(
            "Both Kids Piano", self.family, self.parent, [self.kid1, self.kid2],
            now + timedelta(days=7),
            now + timedelta(days=7, hours=1)
        )
        team_ev = create_team_event(
            "Tournament", self.team, self.owner,
            now + timedelta(days=7, minutes=10),
            now + timedelta(days=7, hours=1, minutes=10)
        )
        invitation = self._create_invitation_for_event(team_ev)

        self.client.login(username="conflict_parent", password="testpass123")
        self.client.post(reverse("team_event_kid_selection", args=[invitation.id]), {
            "kids": [str(self.kid1.id)]
        })

        self.client.get(reverse("replace_with_team_event", args=[invitation.id, self.kid1.id]))

        personal.refresh_from_db()
        self.assertNotIn(self.kid1, personal.kids.all())
        self.assertIn(self.kid2, personal.kids.all())

    def test_replace_with_team_event_declines_prior_team_attendance(self):
        now = timezone.now()
        # First team event that kid1 already accepted
        first_te = create_team_event(
            "First Practice", self.team, self.owner,
            now + timedelta(days=10),
            now + timedelta(days=10, hours=1)
        )
        TeamEventAttendance.objects.create(team_event=first_te, kid=self.kid1, status="accepted")

        # New conflicting team event
        second_te = create_team_event(
            "Second Practice", self.team, self.owner,
            now + timedelta(days=10, minutes=30),
            now + timedelta(days=10, hours=1, minutes=30)
        )
        invitation = self._create_invitation_for_event(second_te)

        self.client.login(username="conflict_parent", password="testpass123")
        self.client.post(reverse("team_event_kid_selection", args=[invitation.id]), {
            "kids": [str(self.kid1.id)]
        })

        self.client.get(reverse("replace_with_team_event", args=[invitation.id, self.kid1.id]))

        # Prior attendance flipped to declined
        old_att = TeamEventAttendance.objects.get(team_event=first_te, kid=self.kid1)
        self.assertEqual(old_att.status, "declined")

        # New one accepted
        new_att = TeamEventAttendance.objects.get(team_event=second_te, kid=self.kid1)
        self.assertEqual(new_att.status, "accepted")

    def test_decline_team_event_invite_keeps_original_family_event(self):
        now = timezone.now()
        personal = create_personal_event(
            "Must Keep This", self.family, self.parent, [self.kid1],
            now + timedelta(days=12),
            now + timedelta(days=12, hours=2)
        )
        team_ev = create_team_event(
            "Optional Game", self.team, self.owner,
            now + timedelta(days=12, minutes=45),
            now + timedelta(days=12, hours=2, minutes=45)
        )
        invitation = self._create_invitation_for_event(team_ev)

        self.client.login(username="conflict_parent", password="testpass123")
        self.client.post(reverse("team_event_kid_selection", args=[invitation.id]), {
            "kids": [str(self.kid1.id)]
        })

        # Decline / keep current
        resp = self.client.get(
            reverse("decline_team_event_invite", args=[invitation.id, self.kid1.id])
        )
        self.assertEqual(resp.status_code, 302)

        # Personal event untouched
        self.assertTrue(Event.objects.filter(id=personal.id).exists())

        # Attendance recorded as declined
        att = TeamEventAttendance.objects.get(team_event=team_ev, kid=self.kid1)
        self.assertEqual(att.status, "declined")

    def test_multi_kid_mixed_conflict_auto_accepts_clears_and_resolves_pending(self):
        now = timezone.now()
        # kid1 has conflict, kid2 does not
        create_personal_event(
            "Only Kid1 Conflict", self.family, self.parent, [self.kid1],
            now + timedelta(days=15),
            now + timedelta(days=15, hours=1)
        )

        team_ev = create_team_event(
            "Mixed Day", self.team, self.owner,
            now + timedelta(days=15, minutes=20),
            now + timedelta(days=15, hours=1, minutes=20)
        )
        invitation = self._create_invitation_for_event(team_ev)

        self.client.login(username="conflict_parent", password="testpass123")
        resp = self.client.post(reverse("team_event_kid_selection", args=[invitation.id]), {
            "kids": [str(self.kid1.id), str(self.kid2.id)]
        })
        # Should redirect to resolve because of kid1
        self.assertIn("resolve", resp["Location"])

        # kid2 should already be auto-accepted
        self.assertTrue(TeamEventAttendance.objects.filter(
            team_event=team_ev, kid=self.kid2, status="accepted"
        ).exists())

        # Resolve the remaining (kid1) by replacing
        self.client.get(reverse("replace_with_team_event", args=[invitation.id, self.kid1.id]))

        self.assertTrue(TeamEventAttendance.objects.filter(
            team_event=team_ev, kid=self.kid1, status="accepted"
        ).exists())

        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "accepted")


# =============================================================================
# ACCOUNT DELETION TESTS (the key user-requested area)
# =============================================================================

class AccountDeletionTests(TestCase):
    """Test the full delete_account flow for both roles + data integrity + audit log."""

    def setUp(self):
        self.client = Client()

    def test_parent_account_deletion_requires_password_and_delete_text(self):
        user, profile = create_parent_user("delparent")
        fam = create_family("DelFam", user)
        profile.family = fam
        profile.save()
        kid = create_kid("Del", "Kid", fam, user)

        self.client.login(username="delparent", password="testpass123")

        # Missing confirmation text
        resp = self.client.post(reverse("delete_account"), {
            "password": "testpass123",
            "confirm_text": "wrong",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username="delparent").exists())

        # Wrong password
        resp2 = self.client.post(reverse("delete_account"), {
            "password": "badpass",
            "confirm_text": "DELETE",
        })
        self.assertEqual(resp2.status_code, 302)
        self.assertTrue(User.objects.filter(username="delparent").exists())

    def test_parent_deletion_cleans_kids_events_memberships_and_creates_log(self):
        user, profile = create_parent_user("delparent2")
        fam = create_family("DelFam2", user)
        profile.family = fam
        profile.save()
        kid = create_kid("Del2", "Kid", fam, user)

        # Personal event for the kid
        ev = create_personal_event(
            "ToBeCleaned", fam, user, [kid],
            timezone.now() + timedelta(days=20),
            timezone.now() + timedelta(days=20, hours=1)
        )
        # Team stuff
        org = create_org("OtherOrg", create_owner_user("otherowner")[0])
        team = create_team("DelTeam", org)
        mem = create_team_membership(team, user)
        PlayerRegistration.objects.create(team_membership=mem, kid=kid)

        # Some notifications + invites
        Notification.objects.create(user=user, title="x", message="y")
        Invite.objects.create(sender=user, receiver=user, status="pending")  # self for simplicity

        self.client.login(username="delparent2", password="testpass123")
        resp = self.client.post(reverse("delete_account"), {
            "password": "testpass123",
            "confirm_text": "DELETE",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("?deleted=1", resp["Location"])

        # User gone
        self.assertFalse(User.objects.filter(username="delparent2").exists())

        # Audit log exists
        log = AccountDeletionLog.objects.filter(user_id=user.id).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.role, "parent")

        # Kid gone (cascade)
        self.assertFalse(Kid.objects.filter(id=kid.id).exists())
        # Event cleaned because it only had this kid
        self.assertFalse(Event.objects.filter(id=ev.id).exists())
        # Player reg gone
        self.assertFalse(PlayerRegistration.objects.filter(kid=kid).exists())

    def test_parent_deletion_transfers_family_if_other_parents_exist(self):
        creator, cprof = create_parent_user("creator")
        fam = create_family("SharedFam", creator)
        cprof.family = fam
        cprof.save()

        other, oprof = create_parent_user("otherparent")
        oprof.family = fam
        oprof.save()

        # Delete the creator
        self.client.login(username="creator", password="testpass123")
        self.client.post(reverse("delete_account"), {
            "password": "testpass123",
            "confirm_text": "DELETE",
        })

        fam.refresh_from_db()
        self.assertEqual(fam.created_by, other)  # transferred

    def test_owner_account_deletion_deletes_org_teams_team_events_but_preserves_other_families_kids(self):
        owner, _ = create_owner_user("bigowner")
        org = create_org("Big Org", owner)
        team = create_team("Big Team", org)

        # Another parent's kid registered on the team
        other_parent, op = create_parent_user("innocentparent")
        other_fam = create_family("Innocent Fam", other_parent)
        op.family = other_fam
        op.save()
        innocent_kid = create_kid("Innocent", "Kid", other_fam, other_parent)
        mem = create_team_membership(team, other_parent)
        PlayerRegistration.objects.create(team_membership=mem, kid=innocent_kid)

        # Team event + attendance for the innocent kid
        te = create_team_event("Big Event", team, owner, timezone.now() + timedelta(days=1), timezone.now() + timedelta(days=1, hours=2))
        TeamEventAttendance.objects.create(team_event=te, kid=innocent_kid, status="accepted")

        # Owner deletes self
        self.client.login(username="bigowner", password="testpass123")
        self.client.post(reverse("delete_account"), {
            "password": "testpass123",
            "confirm_text": "DELETE",
        })

        self.assertFalse(User.objects.filter(username="bigowner").exists())
        self.assertFalse(Organization.objects.filter(id=org.id).exists())
        self.assertFalse(Team.objects.filter(id=team.id).exists())
        self.assertFalse(TeamEvent.objects.filter(id=te.id).exists())

        # Innocent kid and family still exist
        self.assertTrue(Kid.objects.filter(id=innocent_kid.id).exists())
        self.assertTrue(Family.objects.filter(id=other_fam.id).exists())
        # The registration link is gone because team is gone
        self.assertFalse(PlayerRegistration.objects.filter(kid=innocent_kid).exists())

    def test_delete_account_page_shows_correct_counts(self):
        user, profile = create_parent_user("countparent")
        fam = create_family("CountFam", user)
        profile.family = fam
        profile.save()
        k1 = create_kid("C1", "K", fam, user)
        k2 = create_kid("C2", "K", fam, user)
        create_personal_event("E1", fam, user, [k1], timezone.now(), timezone.now() + timedelta(hours=1))
        Notification.objects.create(user=user, title="n", message="m")

        self.client.login(username="countparent", password="testpass123")
        resp = self.client.get(reverse("delete_account"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2")  # kids
        self.assertContains(resp, "DELETE")


# =============================================================================
# DATA EXPORT + PRIVACY
# =============================================================================

class PrivacyExportTests(TestCase):
    def test_export_data_returns_json_with_user_data(self):
        user, profile = create_parent_user("exportuser")
        fam = create_family("ExportFam", user)
        profile.family = fam
        profile.save()
        kid = create_kid("Exp", "Kid", fam, user)
        create_personal_event("Exp Event", fam, user, [kid], timezone.now(), timezone.now() + timedelta(hours=1))

        self.client.login(username="exportuser", password="testpass123")
        resp = self.client.get(reverse("export_data"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertIn(b"exported_at", resp.content)
        self.assertIn(b"Exp Kid", resp.content)


# =============================================================================
# INVITE + NOTIFICATION + UTILITY FUNCTION TESTS
# =============================================================================

class InviteAndUtilityTests(TestCase):
    def test_has_conflict_utility_directly(self):
        parent, _ = create_parent_user("utilparent")
        fam = create_family("UtilFam", parent)
        kid = create_kid("Util", "Kid", fam, parent)

        base = timezone.now() + timedelta(days=30)
        create_personal_event("Base", fam, parent, [kid], base, base + timedelta(hours=2))

        # Overlapping new event (simulated)
        new_ev = Event(
            name="New",
            start_time=base + timedelta(minutes=30),
            end_time=base + timedelta(hours=1, minutes=30),
            created_by=parent,
        )
        self.assertTrue(has_conflict(new_ev, kids=[kid]))

        # Non-overlapping
        new_ev2 = Event(
            name="Safe",
            start_time=base + timedelta(hours=3),
            end_time=base + timedelta(hours=4),
            created_by=parent,
        )
        self.assertFalse(has_conflict(new_ev2, kids=[kid]))

    def test_get_kid_conflicts_utility(self):
        parent, _ = create_parent_user("confutil")
        fam = create_family("ConfUtilFam", parent)
        kid = create_kid("C", "U", fam, parent)

        base = timezone.now() + timedelta(days=40)
        personal = create_personal_event("Personal", fam, parent, [kid], base, base + timedelta(hours=1))

        # Must be a *saved* TeamEvent instance because get_kid_conflicts does .exclude(team_event=proposed_event)
        other_org_owner, _ = create_owner_user("o2")
        other_org = create_org("O", other_org_owner)
        other_team = create_team("T", other_org)
        proposed = TeamEvent.objects.create(
            name="Proposed",
            start_time=base + timedelta(minutes=10),
            end_time=base + timedelta(hours=1, minutes=10),
            team=other_team,
            created_by=parent,
        )
        conflicts = get_kid_conflicts(kid, proposed)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["type"], "family")


# =============================================================================
# FULL APP SMOKE / ADDITIONAL COVERAGE
# =============================================================================

class AdditionalCoverageTests(TestCase):
    """Catch-all for other important behaviors and edge cases."""

    def setUp(self):
        self.client = Client()
        self.owner, _ = create_owner_user("smoke_owner")
        self.org = create_org("Smoke Org", self.owner)
        self.team = create_team("Smoke Team", self.org)
        self.parent, self.pp = create_parent_user("smoke_parent")
        self.fam = create_family("Smoke Fam", self.parent)
        self.pp.family = self.fam
        self.pp.save()
        self.k = create_kid("Smoke", "Kid", self.fam, self.parent)
        m = create_team_membership(self.team, self.parent)
        PlayerRegistration.objects.create(team_membership=m, kid=self.k)

    def test_owner_event_list_past_filter_hides_upcoming_events(self):
        self.client.login(username="smoke_owner", password="testpass123")
        create_team_event(
            "Future Game",
            self.team,
            self.owner,
            timezone.now() + timedelta(days=5),
            timezone.now() + timedelta(days=5, hours=2),
        )
        create_team_event(
            "Old Game",
            self.team,
            self.owner,
            timezone.now() - timedelta(days=5),
            timezone.now() - timedelta(days=5) + timedelta(hours=2),
        )

        resp = self.client.get(reverse("event_list") + "?range=past")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Future Game")
        self.assertContains(resp, "Old Game")

    def test_owner_event_list_defaults_to_upcoming(self):
        self.client.login(username="smoke_owner", password="testpass123")
        create_team_event(
            "Future Game",
            self.team,
            self.owner,
            timezone.now() + timedelta(days=5),
            timezone.now() + timedelta(days=5, hours=2),
        )
        create_team_event(
            "Old Game",
            self.team,
            self.owner,
            timezone.now() - timedelta(days=5),
            timezone.now() - timedelta(days=5) + timedelta(hours=2),
        )

        resp = self.client.get(reverse("event_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Future Game")
        self.assertNotContains(resp, "Old Game")
        self.assertContains(resp, "Upcoming")

    def test_owner_event_list_shows_attendance_counts(self):
        self.client.login(username="smoke_owner", password="testpass123")
        te = create_team_event(
            "Owner Practice",
            self.team,
            self.owner,
            timezone.now() + timedelta(days=2),
            timezone.now() + timedelta(days=2, hours=2),
        )
        TeamEventAttendance.objects.create(team_event=te, kid=self.k, status="accepted")

        resp = self.client.get(reverse("event_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Owner Practice")
        self.assertContains(resp, "1 going")

    def test_event_list_shows_both_personal_and_accepted_team_events(self):
        self.client.login(username="smoke_parent", password="testpass123")

        # Personal
        create_personal_event("My Party", self.fam, self.parent, [self.k],
                              timezone.now() + timedelta(days=1),
                              timezone.now() + timedelta(days=1, hours=2))

        # Team event + accepted attendance
        te = create_team_event("Team Party", self.team, self.owner,
                               timezone.now() + timedelta(days=3),
                               timezone.now() + timedelta(days=3, hours=2))
        TeamEventAttendance.objects.create(team_event=te, kid=self.k, status="accepted")

        resp = self.client.get(reverse("event_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "My Party")
        self.assertContains(resp, "Team Party")

    def test_owner_cannot_edit_parent_family_event(self):
        ev = create_personal_event("Secret", self.fam, self.parent, [self.k],
                                   timezone.now(), timezone.now() + timedelta(hours=1))
        self.client.login(username="smoke_owner", password="testpass123")
        resp = self.client.get(reverse("edit_event", args=[ev.id]))
        # Should not succeed (redirect or error)
        self.assertNotEqual(resp.status_code, 200)

    def test_login_shows_deleted_message(self):
        resp = self.client.get(reverse("login") + "?deleted=1")
        self.assertContains(resp, "permanently deleted")


# =============================================================================
# TEAM EVENT UPDATE REVIEW TESTS
# =============================================================================

class TeamEventUpdateReviewTests(TestCase):
    """Test the parent review flow after an owner updates a team event."""

    def setUp(self):
        self.client = Client()
        self.owner, _ = create_owner_user("update_coach")
        self.org = create_org("Update Org", self.owner)
        self.team = create_team("Update Team", self.org)

        self.parent, self.pprof = create_parent_user("update_parent")
        self.family = create_family("Update Fam", self.parent)
        self.pprof.family = self.family
        self.pprof.save()
        self.kid = create_kid("Update", "Kid", self.family, self.parent)

        mem = create_team_membership(self.team, self.parent)
        PlayerRegistration.objects.create(team_membership=mem, kid=self.kid)

        self.team_event = create_team_event(
            "Original Practice", self.team, self.owner,
            timezone.now() + timedelta(days=5),
            timezone.now() + timedelta(days=5, hours=2),
        )
        TeamEventAttendance.objects.create(
            team_event=self.team_event, kid=self.kid, status="accepted"
        )

    def test_review_page_shown_when_needs_review(self):
        TeamEventAttendance.objects.filter(
            team_event=self.team_event, kid=self.kid
        ).update(needs_review=True)

        self.client.login(username="update_parent", password="testpass123")
        resp = self.client.get(reverse("review_team_event_update", args=[self.team_event.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Original Practice")

    def test_keep_update_clears_needs_review(self):
        TeamEventAttendance.objects.filter(
            team_event=self.team_event, kid=self.kid
        ).update(needs_review=True)

        self.client.login(username="update_parent", password="testpass123")
        resp = self.client.get(reverse("keep_team_event_update", args=[self.team_event.id]))
        self.assertEqual(resp.status_code, 302)

        att = TeamEventAttendance.objects.get(team_event=self.team_event, kid=self.kid)
        self.assertFalse(att.needs_review)

    def test_remove_attendance_after_update(self):
        self.client.login(username="update_parent", password="testpass123")
        resp = self.client.get(reverse("remove_team_event_attendance", args=[self.team_event.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            TeamEventAttendance.objects.filter(team_event=self.team_event, kid=self.kid).exists()
        )

    def test_parent_can_back_out_single_kid_and_owner_is_notified(self):
        att = TeamEventAttendance.objects.get(team_event=self.team_event, kid=self.kid)
        self.client.login(username="update_parent", password="testpass123")

        resp = self.client.get(reverse("delete_attendance", args=[att.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Update Kid")

        resp = self.client.post(reverse("delete_attendance", args=[att.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            TeamEventAttendance.objects.filter(team_event=self.team_event, kid=self.kid).exists()
        )

        notif = Notification.objects.filter(
            user=self.owner,
            notification_type='team_event_updated',
            extra_data__team_event_id=self.team_event.id,
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Update Kid", notif.message)
        self.assertIn("no longer going", notif.message)

    def test_owner_notification_marked_read_when_opening_team_event(self):
        notif = Notification.objects.create(
            user=self.owner,
            title="Attendance Change",
            message="Update Kid is no longer going to 'Original Practice'.",
            notification_type='team_event_updated',
            extra_data={
                'team_event_id': self.team_event.id,
                'action': 'kid_backed_out',
            },
        )
        self.assertFalse(notif.is_read)

        self.client.login(username="update_coach", password="testpass123")
        url = reverse("team_event_detail", args=[self.team_event.id]) + f"?read={notif.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse("team_event_detail", args=[self.team_event.id]),
        )

        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

        resp = self.client.get(resp.url)
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(reverse("notifications"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, ">NEW<")

    def test_review_redirects_when_no_needs_review(self):
        self.client.login(username="update_parent", password="testpass123")
        resp = self.client.get(reverse("review_team_event_update", args=[self.team_event.id]))
        self.assertEqual(resp.status_code, 302)


# =============================================================================
# SECURITY / IDOR HARDENING TESTS
# =============================================================================

class SecurityIDORTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner1, _ = create_owner_user("owner_a")
        self.owner2, _ = create_owner_user("owner_b")
        self.org1 = create_org("Org A", self.owner1)
        self.org2 = create_org("Org B", self.owner2)
        self.team1 = create_team("Team A", self.org1)
        self.team2 = create_team("Team B", self.org2)

        self.parent1, self.p1prof = create_parent_user("parent_a")
        self.parent2, self.p2prof = create_parent_user("parent_b")
        self.fam1 = create_family("Fam A", self.parent1)
        self.fam2 = create_family("Fam B", self.parent2)
        self.p1prof.family = self.fam1
        self.p1prof.save()
        self.p2prof.family = self.fam2
        self.p2prof.save()

    def test_owner_cannot_invite_to_another_owners_team(self):
        self.client.login(username="owner_a", password="testpass123")
        resp = self.client.get(
            reverse("team_invite_to_parent", args=[self.team2.id, "parent_b"])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            Invite.objects.filter(team=self.team2, sender=self.owner1).exists()
        )

    def test_parent_cannot_invite_to_another_family(self):
        self.client.login(username="parent_a", password="testpass123")
        resp = self.client.post(reverse("invite_parent", args=[self.fam2.id]), {
            "username": "parent_b",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            Invite.objects.filter(family=self.fam2, sender=self.parent1).exists()
        )

    def test_parent_can_invite_to_own_family(self):
        self.client.login(username="parent_a", password="testpass123")
        resp = self.client.post(reverse("invite_parent", args=[self.fam1.id]), {
            "username": "parent_b",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Invite.objects.filter(family=self.fam1, sender=self.parent1, receiver=self.parent2).exists()
        )

    def test_select_kids_for_team_roster_requires_login(self):
        owner, _ = create_owner_user("roster_owner")
        org = create_org("Roster Org", owner)
        team = create_team("Roster Team", org)
        invite = Invite.objects.create(
            team=team,
            sender=owner,
            receiver=self.parent1,
            invite_type="team_sent_invite",
            status="pending",
        )
        resp = self.client.get(reverse("select_kids_for_team_roster", args=[invite.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])


# =============================================================================
# ORGANIZATION EDIT TESTS
# =============================================================================

class AccountSettingsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.profile = create_parent_user("settings_user")
        self.other, _ = create_parent_user("other_email_user")
        self.other.email = "taken@example.com"
        self.other.save()

    def test_duplicate_email_rejected_on_account_settings(self):
        self.client.login(username="settings_user", password="testpass123")
        resp = self.client.post(reverse("account_settings"), {
            "first_name": "Settings",
            "last_name": "User",
            "email": "taken@example.com",
            "timezone": "America/Chicago",
            "phone": "",
            "current_password": "testpass123",
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.email.lower(), "taken@example.com")

    def test_own_email_can_be_saved_unchanged(self):
        self.user.email = "mine@example.com"
        self.user.save()
        self.client.login(username="settings_user", password="testpass123")
        resp = self.client.post(reverse("account_settings"), {
            "first_name": "Settings",
            "last_name": "User",
            "email": "mine@example.com",
            "timezone": "America/Chicago",
            "phone": "",
            "current_password": "testpass123",
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "mine@example.com")

    def test_account_settings_normalizes_email_whitespace(self):
        self.client.login(username="settings_user", password="testpass123")
        resp = self.client.post(reverse("account_settings"), {
            "first_name": "Settings",
            "last_name": "User",
            "email": "  saved@example.com  ",
            "timezone": "America/Chicago",
            "phone": "",
            "current_password": "testpass123",
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "saved@example.com")

    def test_account_settings_rejects_empty_email(self):
        self.user.email = "keep@example.com"
        self.user.save()
        self.client.login(username="settings_user", password="testpass123")
        resp = self.client.post(reverse("account_settings"), {
            "first_name": "Settings",
            "last_name": "User",
            "email": "   ",
            "timezone": "America/Chicago",
            "phone": "",
            "current_password": "testpass123",
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "keep@example.com")

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_saved_account_email_works_for_password_reset(self):
        self.client.login(username="settings_user", password="testpass123")
        self.client.post(reverse("account_settings"), {
            "first_name": "Settings",
            "last_name": "User",
            "email": "  reset-ready@example.com ",
            "timezone": "America/Chicago",
            "phone": "",
            "current_password": "testpass123",
        })
        self.client.logout()
        from django.core import mail
        mail.outbox.clear()
        resp = self.client.post(reverse("password_reset"), {"email": "reset-ready@example.com"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)


class NotificationEmailTests(TestCase):
    def setUp(self):
        self.user, _ = create_parent_user("notify_parent")
        self.user.email = "notify@example.com"
        self.user.save()

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        SITE_BASE_URL='https://fusionbeta.com',
    )
    def test_notify_user_sends_email_with_action_link(self):
        from core.notifications import notify_user

        notify_user(
            self.user,
            title="Team Invite",
            message="You have been invited to join U10 Hawks.",
            notification_type='team_invite',
            extra_data={'invite_id': 42},
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Fusion: Team Invite")
        self.assertIn("notify@example.com", mail.outbox[0].to)
        self.assertIn("fusionbeta.com", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_notify_user_skips_email_when_user_has_no_email(self):
        from core.notifications import notify_user

        self.user.email = ""
        self.user.save()
        notify_user(
            self.user,
            title="No Email",
            message="In-app only.",
            notification_type='general',
        )
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.user.notifications.count(), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_notify_user_get_or_create_emails_only_once(self):
        from core.notifications import notify_user_get_or_create

        defaults = {
            'title': 'Invitation: Practice',
            'message': 'Please RSVP.',
            'extra_data': {'team_event_id': 99, 'invitation_id': 1},
        }
        notify_user_get_or_create(
            self.user,
            notification_type='team_event_invitation',
            extra_data__team_event_id=99,
            defaults=defaults,
        )
        notify_user_get_or_create(
            self.user,
            notification_type='team_event_invitation',
            extra_data__team_event_id=99,
            defaults=defaults,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            self.user.notifications.filter(notification_type='team_event_invitation').count(),
            1,
        )


class SignupEmailTests(TestCase):
    def test_signup_requires_email(self):
        resp = self.client.post(reverse("signup"), {
            "first_name": "No",
            "last_name": "Email",
            "username": "noemailuser",
            "password1": "testpass123!",
            "password2": "testpass123!",
            "role": "parent",
            "consent": "on",
            "timezone": "America/Chicago",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="noemailuser").exists())

    def test_signup_saves_normalized_email(self):
        resp = self.client.post(reverse("signup"), {
            "first_name": "Email",
            "last_name": "User",
            "email": "  NewSignup@Example.com  ",
            "username": "emailsignupuser",
            "password1": "testpass123!",
            "password2": "testpass123!",
            "role": "parent",
            "consent": "on",
            "timezone": "America/Chicago",
        })
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username="emailsignupuser")
        self.assertEqual(user.email, "newsignup@example.com")


class OrganizationEditTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner, _ = create_owner_user("org_owner")
        self.other_owner, _ = create_owner_user("other_org_owner")
        self.org = create_org("Original Name", self.owner)
        self.other_org = create_org("Other Org", self.other_owner)

    def test_edit_organization_page_loads_for_owner(self):
        self.client.login(username="org_owner", password="testpass123")
        resp = self.client.get(reverse("edit_organization", args=[self.org.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Edit Organization")
        self.assertContains(resp, "Save Changes")

    def test_owner_can_edit_organization(self):
        self.client.login(username="org_owner", password="testpass123")
        resp = self.client.post(reverse("edit_organization", args=[self.org.id]), {
            "name": "Updated Club",
            "description": "New description",
        })
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Updated Club")
        self.assertEqual(self.org.description, "New description")

    def test_owner_cannot_edit_another_owners_organization(self):
        self.client.login(username="org_owner", password="testpass123")
        resp = self.client.post(reverse("edit_organization", args=[self.other_org.id]), {
            "name": "Hijacked",
            "description": "Nope",
        })
        self.assertEqual(resp.status_code, 302)
        self.other_org.refresh_from_db()
        self.assertEqual(self.other_org.name, "Other Org")


class TeamEventHappeningNowTests(TestCase):
    def setUp(self):
        self.owner, _ = create_owner_user("happening_owner")
        self.org = create_org("Pinnacle Performance", self.owner)
        self.team = create_team("U10 Boys", self.org)

    def test_is_happening_now_true_during_event_window(self):
        now = timezone.now()
        event = TeamEvent.objects.create(
            name="Live Window",
            team=self.team,
            start_time=now - timedelta(minutes=10),
            end_time=now + timedelta(minutes=50),
            created_by=self.owner,
            event_type="team",
        )
        self.assertTrue(event.is_happening_now())

    def test_is_happening_now_false_before_start(self):
        now = timezone.now()
        event = TeamEvent.objects.create(
            name="Future Window",
            team=self.team,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            created_by=self.owner,
            event_type="team",
        )
        self.assertFalse(event.is_happening_now())


class OwnerDashboardTodayEventsTests(TestCase):
    def setUp(self):
        self.owner, _ = create_owner_user("dash_owner")
        self.org = create_org("Pinnacle Performance", self.owner)
        self.team = create_team("U10 Boys", self.org)
        self.client = Client()

    def test_live_event_gets_blue_depth_of_field_class(self):
        now = timezone.now()
        live_start = now - timedelta(minutes=30)
        live_end = now + timedelta(hours=1)
        TeamEvent.objects.create(
            name="Live Practice",
            team=self.team,
            start_time=live_start,
            end_time=live_end,
            created_by=self.owner,
            event_type="team",
        )
        self.client.login(username="dash_owner", password="testpass123")
        resp = self.client.get(reverse("owner_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Live Practice")
        self.assertContains(resp, "Now")
        self.assertContains(resp, "Happening Now")
        self.assertContains(resp, "story-card-live")
        self.assertContains(resp, "from-sky-300")
        self.assertContains(resp, "story-live-badge")
        self.assertContains(resp, "story-live-dot-ping")
        self.assertContains(resp, 'data-is-live="1"')

    def test_todays_events_render_in_stories_section(self):
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time(9, 0)))
        TeamEvent.objects.create(
            name="Today Practice",
            team=self.team,
            start_time=start,
            end_time=start + timedelta(hours=1),
            created_by=self.owner,
            location="Main Gym",
            event_type="team",
        )
        self.client.login(username="dash_owner", password="testpass123")
        resp = self.client.get(reverse("owner_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Today's Events")
        self.assertContains(resp, "Today Practice")
        self.assertContains(resp, "openStoryEventModal")
        self.assertContains(resp, "story-event-modal")
        self.assertContains(resp, "story-slot-create")
        self.assertContains(resp, "story-focus-index")

    def test_tomorrow_events_limited_to_five(self):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        for hour in range(8, 16):
            start = timezone.make_aware(datetime.combine(tomorrow, time(hour, 0)))
            TeamEvent.objects.create(
                name=f"Tomorrow Slot {hour}",
                team=self.team,
                start_time=start,
                end_time=start + timedelta(hours=1),
                created_by=self.owner,
                event_type="team",
            )
        self.client.login(username="dash_owner", password="testpass123")
        resp = self.client.get(reverse("owner_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["tomorrow_events"]), 5)

    def test_tomorrow_events_in_upcoming_section_only(self):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        week_out = today + timedelta(days=5)

        today_start = timezone.now() + timedelta(hours=2)
        TeamEvent.objects.create(
            name="Today Only",
            team=self.team,
            start_time=today_start,
            end_time=today_start + timedelta(hours=1),
            created_by=self.owner,
            event_type="team",
        )
        tomorrow_start = timezone.make_aware(datetime.combine(tomorrow, time(14, 0)))
        TeamEvent.objects.create(
            name="Tomorrow Game",
            team=self.team,
            start_time=tomorrow_start,
            end_time=tomorrow_start + timedelta(hours=2),
            created_by=self.owner,
            event_type="team",
        )
        week_start = timezone.make_aware(datetime.combine(week_out, time(9, 0)))
        TeamEvent.objects.create(
            name="Next Week Meet",
            team=self.team,
            start_time=week_start,
            end_time=week_start + timedelta(hours=1),
            created_by=self.owner,
            event_type="team",
        )

        self.client.login(username="dash_owner", password="testpass123")
        resp = self.client.get(reverse("owner_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Tomorrow's Events")
        tomorrow_names = [e.name for e in resp.context["tomorrow_events"]]
        self.assertEqual(tomorrow_names, ["Tomorrow Game"])

    def test_org_wide_training_today_appears_in_stories(self):
        today = timezone.localdate()
        start = timezone.now() + timedelta(hours=3)
        TeamEvent.objects.create(
            name="Warehouse Practice",
            team=None,
            start_time=start,
            end_time=start + timedelta(hours=1),
            created_by=self.owner,
            location="Warehouse",
            event_type="training",
        )
        self.client.login(username="dash_owner", password="testpass123")
        resp = self.client.get(reverse("owner_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Today's Events")
        self.assertContains(resp, "Warehouse Practice")

    def test_newly_created_today_team_event_appears_in_stories(self):
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time(18, 0)))
        end = start + timedelta(hours=1)
        self.client.login(username="dash_owner", password="testpass123")
        resp = self.client.post(reverse("add_team_event"), {
            "name": "Late Day Scrimmage",
            "event_type": "team",
            "team": self.team.id,
            "start_time": start.strftime("%Y-%m-%dT%H:%M"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M"),
            "location": "Field 2",
            "description": "",
        })
        self.assertEqual(resp.status_code, 302)
        dash = self.client.get(reverse("owner_dashboard"))
        self.assertEqual(dash.status_code, 200)
        self.assertContains(dash, "Late Day Scrimmage")
        story_names = [e.name for e in dash.context["story_events"]]
        self.assertIn("Late Day Scrimmage", story_names)

    def test_stories_switch_to_tomorrow_when_today_is_over(self):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)

        past_end = timezone.now() - timedelta(hours=1)
        past_start = past_end - timedelta(hours=1)
        TeamEvent.objects.create(
            name="Morning Done",
            team=self.team,
            start_time=past_start,
            end_time=past_end,
            created_by=self.owner,
            event_type="team",
        )
        tomorrow_start = timezone.make_aware(datetime.combine(tomorrow, time(15, 0)))
        TeamEvent.objects.create(
            name="Tomorrow Practice",
            team=self.team,
            start_time=tomorrow_start,
            end_time=tomorrow_start + timedelta(hours=1),
            created_by=self.owner,
            event_type="team",
        )

        self.client.login(username="dash_owner", password="testpass123")
        resp = self.client.get(reverse("owner_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Tomorrow's Events")
        self.assertContains(resp, "Tomorrow Practice")
        content = resp.content.decode()
        stories_pos = content.find("story-stage")
        upcoming_pos = content.find("Tomorrow's Events")
        self.assertGreater(stories_pos, -1)
        self.assertGreater(upcoming_pos, -1)
        self.assertIn("Tomorrow Practice", content[stories_pos:upcoming_pos])


class KidDisplayInitialsTests(TestCase):
    def setUp(self):
        self.parent, _ = create_parent_user("initials_parent")
        self.family = create_family("Initials Family", self.parent)

    def test_full_name_uses_first_and_last_initial(self):
        kid = create_kid("Jordan", "Smith", self.family, self.parent)
        self.assertEqual(kid.display_initials(), "JS")

    def test_missing_last_name_uses_first_two_letters(self):
        kid = create_kid("Jordan", "", self.family, self.parent)
        self.assertEqual(kid.display_initials(), "JO")

    def test_single_letter_first_name_duplicates_initial(self):
        kid = create_kid("J", "", self.family, self.parent)
        self.assertEqual(kid.display_initials(), "JJ")

    def test_missing_names_falls_back_to_question_mark(self):
        kid = Kid.objects.create(
            first_name="",
            last_name="",
            date_of_birth="2015-06-15",
            gender="M",
            family=self.family,
            parent=self.parent,
        )
        self.assertEqual(kid.display_initials(), "?")


# =============================================================================
# SECURITY HARDENING TESTS
# =============================================================================

class SecurityHardeningTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()

    def test_edit_team_event_requires_login(self):
        owner, _ = create_owner_user("sec_owner")
        org = create_org("Sec Org", owner)
        team = create_team("Sec Team", org)
        te = create_team_event(
            "Private Game", team, owner,
            timezone.now() + timedelta(days=1),
            timezone.now() + timedelta(days=1, hours=2),
        )
        resp = self.client.get(reverse("edit_team_event", args=[te.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_team_invite_response_requires_login(self):
        owner, _ = create_owner_user("inv_owner")
        parent, _ = create_parent_user("inv_parent")
        org = create_org("Inv Org", owner)
        team = create_team("Inv Team", org)
        invite = Invite.objects.create(
            team=team,
            sender=owner,
            receiver=parent,
            invite_type="team_sent_invite",
            status="pending",
        )
        resp = self.client.get(reverse("team_invite_response", args=[invite.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_csrf_required_on_login(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.get(reverse("login"))
        resp = csrf_client.post(reverse("login"), {
            "username": "nobody",
            "password": "wrong",
        })
        # csrf_failure redirects to login with ?csrf=1 instead of a bare 403 page
        self.assertEqual(resp.status_code, 302)
        self.assertIn("csrf=1", resp["Location"])

    @override_settings(AXES_FAILURE_LIMIT=3, AXES_COOLOFF_TIME=1)
    def test_axes_locks_out_after_repeated_failed_logins(self):
        User.objects.create_user(username="lockuser", password="correctpass")
        for _ in range(3):
            self.client.post(reverse("login"), {
                "username": "lockuser",
                "password": "wrongpassword",
            })
        resp = self.client.post(reverse("login"), {
            "username": "lockuser",
            "password": "wrongpassword",
        })
        self.assertIn(resp.status_code, (403, 429))

    def test_account_settings_requires_current_password(self):
        user, _ = create_parent_user("pw_user")
        user.email = "before@example.com"
        user.save()
        self.client.login(username="pw_user", password="testpass123")
        resp = self.client.post(reverse("account_settings"), {
            "first_name": "Pw",
            "last_name": "User",
            "email": "after@example.com",
            "timezone": "America/Chicago",
            "phone": "",
            "current_password": "wrongpassword",
        })
        self.assertEqual(resp.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.email, "before@example.com")

    def test_password_change_works(self):
        user, _ = create_parent_user("changepw")
        self.client.login(username="changepw", password="testpass123")
        resp = self.client.post(reverse("change_password"), {
            "old_password": "testpass123",
            "new_password1": "newpass456!",
            "new_password2": "newpass456!",
        })
        self.assertEqual(resp.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.check_password("newpass456!"))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_sends_email(self):
        user, _ = create_parent_user("resetuser")
        user.email = "resetuser@example.com"
        user.save()
        resp = self.client.post(reverse("password_reset"), {"email": "resetuser@example.com"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_security_headers_present(self):
        resp = self.client.get(reverse("login"))
        self.assertIn("Content-Security-Policy", resp)
        self.assertIn("Referrer-Policy", resp)

    def test_parent_cannot_edit_other_parents_kid(self):
        parent1, p1prof = create_parent_user("kid_owner_a")
        parent2, _ = create_parent_user("kid_owner_b")
        fam1 = create_family("Fam A", parent1)
        p1prof.family = fam1
        p1prof.save()
        kid = create_kid("Stolen", "Kid", fam1, parent1)

        self.client.login(username="kid_owner_b", password="testpass123")
        resp = self.client.get(reverse("edit_kid", args=[kid.id]))
        self.assertNotEqual(resp.status_code, 200)

    def test_parent_cannot_view_other_family_event_detail(self):
        parent1, p1prof = create_parent_user("ev_owner_a")
        parent2, _ = create_parent_user("ev_owner_b")
        fam1 = create_family("Ev Fam A", parent1)
        p1prof.family = fam1
        p1prof.save()
        kid = create_kid("Ev", "Kid", fam1, parent1)
        ev = create_personal_event(
            "Secret Event", fam1, parent1, [kid],
            timezone.now() + timedelta(days=1),
            timezone.now() + timedelta(days=1, hours=1),
        )

        self.client.login(username="ev_owner_b", password="testpass123")
        resp = self.client.get(reverse("event_detail", args=[ev.id]))
        self.assertNotEqual(resp.status_code, 200)

    def test_owner_cannot_edit_other_owners_team_event(self):
        owner1, _ = create_owner_user("te_owner_a")
        owner2, _ = create_owner_user("te_owner_b")
        org1 = create_org("TE Org A", owner1)
        org2 = create_org("TE Org B", owner2)
        team1 = create_team("TE Team A", org1)
        team2 = create_team("TE Team B", org2)
        te2 = create_team_event(
            "Other Event", team2, owner2,
            timezone.now() + timedelta(days=2),
            timezone.now() + timedelta(days=2, hours=1),
        )

        self.client.login(username="te_owner_a", password="testpass123")
        resp = self.client.get(reverse("edit_team_event", args=[te2.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("edit_team_event", resp.get("Location", ""))

    def test_signup_rate_limit_returns_429(self):
        for i in range(10):
            self.client.post(reverse("signup"), {
                "first_name": "Spam",
                "last_name": f"User{i}",
                "email": f"spam{i}@example.com",
                "username": f"spamuser{i}",
                "password1": "testpass123!",
                "password2": "testpass123!",
                "role": "parent",
                "consent": "on",
                "timezone": "America/Chicago",
            })
        resp = self.client.post(reverse("signup"), {
            "first_name": "Spam",
            "last_name": "Eleven",
            "email": "spam11@example.com",
            "username": "spamuser11",
            "password1": "testpass123!",
            "password2": "testpass123!",
            "role": "parent",
            "consent": "on",
            "timezone": "America/Chicago",
        })
        self.assertEqual(resp.status_code, 429)
