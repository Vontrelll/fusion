# Event.status Field Removal and Replaced Event Cleanup

**Date:** 2026-06-03 (approx, based on session)

## Summary of Change
- Removed the `status` field entirely from the `Event` model (was used for 'active' / 'replaced' / 'cancelled').
- Family Events that get replaced during "Replace & Attend" team event conflict resolution are now **hard-deleted** (for solo-kid events) instead of marked with status='replaced'.
- Multi-kid events continue to just have the specific kid removed from the M2M (no status involved).
- All code that was filtering `Event` queries with `status='active'` was cleaned up to remove the filter (since there is no longer a status; all existing Event rows are treated as current/active).
- 'cancelled' status was defined but never actually used in any code path for Events (only 'replaced' was set), so full removal is clean.
- Existing 'replaced' Event rows (2 found) were deleted as part of the migration to prevent them from suddenly appearing in lists/dashboards after filters were removed.

## Why
- Replaced events were already invisible everywhere:
  - Never shown in event_list, dashboard, etc. (hard filtered).
  - Never considered in conflict checks (has_conflict, get_kid_conflicts, etc.).
  - No history UI, reports, or queries ever filtered *on* the replaced status.
- Leaving dead rows + a status field + special-case ignore logic in conflict functions was unnecessary complexity and "dead data".
- Deleting on replace keeps the DB clean and the model simple: an Event either exists (and participates in schedules/conflicts) or it doesn't.
- Aligns with the replace flow's semantic: the family event is being superseded/removed from the kid's calendar.

## Code Changes
- **core/models.py**: Removed `STATUS_CHOICES` and `status` CharField from Event.
- **core/views.py**:
  - `replace_with_team_event`: Solo-kid family conflicts now do `old_event.delete()` instead of `old_event.status = 'replaced'; save()`.
  - Removed `status='active'` from all `Event.objects.filter(...)` calls (dashboard, event_list, has_conflict personal_events, get_kid_conflicts family_events).
  - Updated/removed docstrings and comments that referenced the old replaced/cancelled status handling for family events (e.g. in has_conflict, get_kid_conflicts, get_conflicts_for_kids, event_list, replace function).
  - Minor comment updates around the delete.
- **New migration**: `core/migrations/0024_remove_event_status_field.py` (RemoveField status from event).
- Data cleanup: Pre-migration deletion of the 2 pre-existing replaced Events.

## DB / Migration Impact
- Ran `makemigrations` and `migrate`.
- The column was dropped.
- Old migration `0022_event_status.py` remains in history (as it should).
- No more status column in core_event table.
- All remaining Event rows participate normally (no invisible replaced ones).

## Behavior Impact / What Still Works
- Creating/editing family events, team events: unchanged.
- Conflict detection (has_conflict, get_*_conflicts): now simply doesn't see non-existent events.
- Event lists and dashboards: show whatever Event rows exist.
- Kid deletion, event deletion UI: unchanged (they delete Events).
- "Replace & Attend" flow: now actually deletes the superseded solo family event (cleaner).
- No breakage to other statuses (Invite.status, TeamEventAttendance.status, etc. untouched).
- get_conflicts_for_kids (the multi-kid wrapper) inherits the simplification via get_kid_conflicts.
- Admin, forms, templates: no changes needed (no direct reliance on Event.status for replaced/cancelled).

## Future Considerations (if needed)
- If history of "what family events were replaced by which team events" is desired later, implement it via:
  - Storing in Notification.extra_data (already used for many team/family actions), or
  - A lightweight separate log model, rather than resurrecting status on Event.
- 'cancelled' distinction is gone for family events; if a user wants to "cancel but remember", they can use description or a different mechanism. Deletion is the simple path.
- The replace success message still says "Replaced X conflicting event(s)..." which is user-facing language for the action and was left unchanged.

## Files Touched
- core/models.py
- core/views.py
- core/migrations/0024_remove_event_status_field.py (generated)
- Pre-cleaned data via shell (no file change)

This change was done conservatively: only Event.status removed, replace logic updated to delete, filters/comments cleaned, data pre-cleaned, migration generated/applied, verified with checks/imports/queries. No other models or unrelated logic touched.

Generated so future Grok sessions have context on why Event has no status and why replaced events are deleted rather than marked.
