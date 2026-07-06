from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Send a Hello World test email via Resend (django-anymail backend).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--to',
            default='darvinvontrell@gmail.com',
            help='Recipient email address.',
        )

    def handle(self, *args, **options):
        if not getattr(settings, 'RESEND_API_KEY', None):
            raise CommandError(
                'RESEND_API_KEY is not set. Add it to your .env file '
                '(replace re_xxxxxxxxx with your real API key).'
            )

        to = options['to']
        send_mail(
            subject='Hello World',
            message='Congrats on sending your first email!',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            html_message=(
                '<p>Congrats on sending your <strong>first email</strong>!</p>'
            ),
        )
        self.stdout.write(self.style.SUCCESS(f'Test email sent to {to}'))
