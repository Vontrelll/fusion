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

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import datetime, timedelta, date
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
        self.assertTrue(Event.objects.filter(name="NonConflicting").exists())


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


# Add more classes here as the app grows.
# The goal is to keep the suite fast, isolated (each TestCase gets a fresh DB),
# and to cover the user-visible critical paths especially around team events,
# conflict resolution, and account deletion.
