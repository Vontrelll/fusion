# Fusion

Fusion is a family + team sports scheduling and roster management web application built with Django.

It helps **parents/families** manage kids, personal events, and respond to team invitations, and helps **team owners/coaches** (organizations) manage teams, rosters, and team-wide events with sophisticated conflict detection and resolution.

## Key Features

- **Dual role system**: `parent` (families + kids + personal calendar) vs `owner` (organizations + teams + rosters + team events). Strict isolation between the two sides.
- **Personal & Family Events**: Parents create events for one or more of their kids with overlap detection.
- **Team Events + Smart Invitations**: Owners create events for a whole team. Parents receive per-kid invitations.
- **Advanced Conflict Resolution Wizard**:
  - When a parent selects kids for a team event that overlaps existing calendar items, the app detects conflicts per kid.
  - Non-conflicting kids are auto-accepted.
  - Conflicting kids go through a dedicated resolution screen (`resolve_team_event_conflict`).
  - "Replace with Team Event" automatically removes the kid from the old family event (or deletes the solo event) or declines a prior team attendance.
  - "Keep Current Event" (decline) preserves the existing commitment.
- **Roster Management**: Parents request to join teams (selecting specific kids). Owners review and approve/decline. Multi-kid support and "add another kid later" flows.
- **Privacy & Compliance (Step 3)**:
  - Full JSON data export for the user's own data.
  - Careful permanent account deletion with double confirmation (password + type `DELETE`).
  - Role-aware deletion: parents lose kids/events/memberships (family may be transferred or deleted); owners lose their entire org + teams + team events (other families' base kid records are preserved).
  - `AccountDeletionLog` audit trail (minimal non-PII) for legal traceability ("right to be forgotten").
- **Notifications**: Rich in-app notifications for invites, updates, cancellations, roster requests, conflict-related actions, etc.
- **Strong scoping & security**: Almost every view hardens lookups by ownership/family/team to prevent IDOR and cross-tenant data leakage.

## Tech Stack

- Django 6 + SQLite (easy local dev; Postgres ready via requirements)
- pytz for timezones
- django-axes for login brute-force protection
- Tailwind + server-rendered templates (no heavy JS SPA)

## Project Structure

```
core/
  models.py          # All domain models (Family, Kid, Event, Team, TeamEvent, Invite, AccountDeletionLog, ...)
  views.py           # Large but well-organized; critical functions include has_conflict, team_event_kid_selection,
                     # resolve_team_event_conflict, replace_with_team_event, _perform_account_deletion, delete_account, etc.
  forms.py
  urls.py
  tests.py           # Comprehensive test suite (see below)
  templates/core/    # All UI templates
fusion/              # Project settings/urls
manage.py
```

## Getting Started (Local Development)

1. **Python environment**
   ```bash
   python -m venv venv
   source venv/bin/activate   # macOS/Linux
   # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```

2. **Database & migrations**
   ```bash
   python manage.py migrate
   ```

3. **Create a superuser (optional, for admin)**
   ```bash
   python manage.py createsuperuser
   ```

4. **Run the dev server**
   ```bash
   python manage.py runserver
   ```

   Visit http://127.0.0.1:8000/

### First-time flows to try
- Sign up as a **parent** → create first family → add kids → create personal events.
- Sign up as an **owner** → create organization → create team(s).
- As a parent, use **Find Teams** to send roster requests.
- As owner, review requests and add kids to rosters.
- Create **Team Events** as owner.
- As parent, respond via the kid selection + conflict resolution flow.

## Running Tests

The project has a growing, thorough test suite focused on the most important user journeys.

```bash
python manage.py test core --verbosity=2
```

Or run everything:
```bash
python manage.py test
```

**Key test classes** (in `core/tests.py`):
- `BasicSecurityTests`, `OwnerParentIsolationTests`
- `FamilyKidEventTests` — kid CRUD, cascades on delete, personal event conflict detection
- `OrgTeamRosterTests` — org/team creation, roster request + owner approval, parent removal from team
- `TeamEventTests` — owner creates team events + invitations, team-level conflict blocking, basic kid selection
- `ConflictResolutionTests` — **the heart of the app**
  - Personal vs team event conflict detection
  - Session-based wizard state (`team_event_pending_kids`, `team_event_original_selection`)
  - `replace_with_team_event` (solo family event deletion, multi-kid removal, prior team attendance decline)
  - `decline_team_event_invite` (keeps original commitment)
  - Mixed multi-kid flows (some auto-accepted, some resolved)
- `AccountDeletionTests` — password + "DELETE" confirmation, parent path (kids/events/memberships/family transfer), owner path (full org wipe while preserving other families' kids), audit log creation, counts on the warning page
- `PrivacyExportTests`
- `InviteAndUtilityTests` — direct testing of `has_conflict`, `get_kid_conflicts`, etc.
- `AdditionalCoverageTests` — event list mixing, permission checks, deleted-account login message

Tests use isolated `TestCase` (transaction rollback per test) and realistic `Client` POST/GET flows + session manipulation where the conflict wizard requires it.

## Important Domain Rules

- An **owner** can never belong to a family (`Profile.save` and helpers enforce this).
- Families are for the parent/kid side only.
- Conflict detection (`has_conflict` / `get_kid_conflicts`) only considers **confirmed** items: personal Events the parent created + `TeamEventAttendance` with `status='accepted'`. Pending invites/declined items do not block.
- When the last kid is removed from a personal event during "replace", the event itself is deleted (keeps the DB clean).
- Account deletion for owners removes the org + everything under it, but **does not delete other parents' Kid or Family rows** (only the registration/attendance links).

## Privacy & Legal Notes

Fusion was built with "right to be forgotten" and children's data (COPPA-style) considerations in mind:
- `data_consent_at` recorded at signup.
- Account deletion produces an `AccountDeletionLog` entry.
- Export is scoped strictly to the requesting user's data.
- Deletion is intentionally destructive and audited.

## Contributing / Extending

- When adding new flows that touch the calendar (personal events, team events, attendance), add or extend conflict tests in `ConflictResolutionTests`.
- Any change to deletion logic must update `AccountDeletionTests` and verify the audit log + cross-role data preservation.
- Keep authorization checks (family scoping, `team__organization__owner`, etc.) explicit in views.

## License

Internal / proprietary for now.

---

Built with care for real families and real sports organizations who need reliable scheduling without surprises.
