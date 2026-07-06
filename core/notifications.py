"""
Central notification helper: in-app Notification row + optional email via Resend.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from .models import Notification, TeamEvent

logger = logging.getLogger(__name__)


def _user_owns_team_event(team_event, user):
    try:
        return team_event.team.organization.owner_id == user.id
    except AttributeError:
        return False


def build_notification_url(notification, *, base_url=None):
    """Full HTTPS link for the notification action (marks read via ?read=)."""
    base = (base_url or getattr(settings, 'SITE_BASE_URL', 'https://fusionbeta.com')).rstrip('/')
    extra = notification.extra_data or {}
    ntype = notification.notification_type
    user = notification.user

    if ntype == 'team_event_updated':
        team_event_id = extra.get('team_event_id') or extra.get('event_id')
        if team_event_id:
            try:
                te = TeamEvent.objects.select_related('team__organization').get(id=team_event_id)
                if _user_owns_team_event(te, user):
                    path = reverse('team_event_detail', args=[team_event_id])
                else:
                    path = reverse('review_team_event_update', args=[team_event_id])
            except TeamEvent.DoesNotExist:
                path = reverse('event_list')
        else:
            path = reverse('event_list')
    elif ntype == 'team_event_invitation':
        invitation_id = extra.get('invitation_id') or extra.get('team_event_id')
        path = (
            reverse('team_event_kid_selection', args=[invitation_id])
            if invitation_id else reverse('notifications')
        )
    elif ntype == 'roster_request':
        invite_id = extra.get('invite_id')
        path = (
            reverse('review_roster_request', args=[invite_id])
            if invite_id else reverse('notifications')
        )
    elif ntype == 'team_invite':
        invite_id = extra.get('invite_id')
        path = (
            reverse('team_invite_response', args=[invite_id])
            if invite_id else reverse('notifications')
        )
    elif ntype in ('family_invite', 'parent_invite', 'family_join_request'):
        invite_id = extra.get('invite_id')
        path = (
            reverse('family_invite_response', args=[invite_id])
            if invite_id else reverse('notifications')
        )
    elif ntype == 'team_event_canceled':
        path = reverse('event_list')
    else:
        path = reverse('notifications')

    separator = '&' if '?' in path else '?'
    return f"{base}{path}{separator}read={notification.id}"


def send_notification_email(user, notification):
    """Send a transactional email for a notification. Best-effort; never raises."""
    email = (getattr(user, 'email', None) or '').strip()
    if not email:
        logger.info(
            'Skipping notification email for user %s (no email on file)',
            user.pk,
        )
        return False

    action_url = build_notification_url(notification)
    context = {
        'title': notification.title,
        'message': notification.message,
        'action_url': action_url,
    }
    subject = f"Fusion: {notification.title}"
    text_body = render_to_string('core/notification_email.txt', context)
    html_body = render_to_string('core/notification_email.html', context)

    try:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html_body,
            fail_silently=False,
        )
        logger.info(
            'Sent notification email type=%s to user=%s',
            notification.notification_type,
            user.pk,
        )
        return True
    except Exception:
        logger.exception(
            'Failed to send notification email type=%s to user=%s',
            notification.notification_type,
            user.pk,
        )
        return False


def notify_user(user, *, title, message, notification_type, extra_data=None):
    """Create an in-app notification and email the user when possible."""
    notification = Notification.objects.create(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
        extra_data=extra_data or {},
    )
    send_notification_email(user, notification)
    return notification


def notify_user_get_or_create(user, *, notification_type, defaults, **lookup):
    """
    get_or_create wrapper that emails only when a new notification row is created.
    """
    notification, created = Notification.objects.get_or_create(
        user=user,
        notification_type=notification_type,
        defaults=defaults,
        **lookup,
    )
    if created:
        send_notification_email(user, notification)
    return notification, created