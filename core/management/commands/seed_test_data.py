from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import (
    Organization, Team, Profile, Family, Kid, TeamMembership, 
    PlayerRegistration, TeamEvent, TeamEventAttendance, TeamEventInvitation,
    Event
)
from django.utils import timezone
from datetime import timedelta, date, datetime, time
import random

class Command(BaseCommand):
    help = 'Seeds comprehensive test data for demo purposes (owner: onefit, parent: darvin)'

    def handle(self, *args, **options):
        self.stdout.write("Starting test data seed...")

        # Get existing owner
        try:
            owner = User.objects.get(username='onefit')
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR("onefit user not found. Please ensure it exists."))
            return

        # Get org
        try:
            org = Organization.objects.get(owner=owner)
        except Organization.DoesNotExist:
            org, _ = Organization.objects.get_or_create(name='Pinnacle Performance', owner=owner)

        self.stdout.write(f"Using Org: {org.name}")

        # Create more teams if not enough
        team_names = [
            ('U8 Boys', 'basketball'),
            ('U8 Girls', 'basketball'),
            ('U10 Boys', 'soccer'),
            ('U10 Girls', 'soccer'),
            ('U12 Boys', 'football'),
            ('U12 Girls', 'football'),
            ('U14 Boys', 'basketball'),
        ]

        teams = []
        for name, sport in team_names:
            team, created = Team.objects.get_or_create(
                name=name,
                organization=org,
                defaults={'sport_type': sport, 'description': f'Test {name} team'}
            )
            teams.append(team)
            if created:
                self.stdout.write(f"Created team: {name}")

        # Ensure darvin parent
        try:
            parent_user = User.objects.get(username='darvin')
        except User.DoesNotExist:
            parent_user = User.objects.create_user('darvin', 'darvinvontrell@gmail.com', 'testpass123')
            parent_user.first_name = 'Darvin'
            parent_user.last_name = 'Vontrell'
            parent_user.save()

        parent_profile, _ = Profile.objects.get_or_create(user=parent_user, defaults={'role': 'parent'})

        family, _ = Family.objects.get_or_create(
            family_name='Vontrell Family',
            defaults={'created_by': parent_user}
        )
        parent_profile.family = family
        parent_profile.save()

        # Darvin's kids
        darvin_kid1, _ = Kid.objects.get_or_create(
            first_name='Liam',
            last_name='Vontrell',
            family=family,
            parent=parent_user,
            defaults={
                'date_of_birth': date(2015, 5, 12),
                'gender': 'M',
                'color': '#3b82f6'
            }
        )

        darvin_kid2, _ = Kid.objects.get_or_create(
            first_name='Mia',
            last_name='Vontrell',
            family=family,
            parent=parent_user,
            defaults={
                'date_of_birth': date(2017, 8, 22),
                'gender': 'F',
                'color': '#ec4899'
            }
        )

        # Create many test kids/families
        first_names_m = ['Noah', 'Oliver', 'James', 'Lucas', 'Henry', 'Alexander', 'William', 'Benjamin', 'Sebastian', 'Jack']
        first_names_f = ['Emma', 'Olivia', 'Ava', 'Sophia', 'Isabella', 'Mia', 'Charlotte', 'Amelia', 'Harper', 'Evelyn']
        last_names = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez']

        created_kids = [darvin_kid1, darvin_kid2]

        for i in range(22):
            gender = random.choice(['M', 'F'])
            first = random.choice(first_names_m if gender == 'M' else first_names_f)
            last = random.choice(last_names)
            
            parent_username = f"parentdemo{i+1}"
            try:
                fam_parent = User.objects.get(username=parent_username)
            except User.DoesNotExist:
                fam_parent = User.objects.create_user(parent_username, f"parent{i+1}@demo.com", 'testpass123')
                fam_parent.first_name = first
                fam_parent.last_name = last
                fam_parent.save()
            
            fam_profile, _ = Profile.objects.get_or_create(user=fam_parent, defaults={'role': 'parent'})
            
            fam, _ = Family.objects.get_or_create(
                family_name=f"{last} Family",
                defaults={'created_by': fam_parent}
            )
            fam_profile.family = fam
            fam_profile.save()
            
            kid, _ = Kid.objects.get_or_create(
                first_name=first,
                last_name=last,
                family=fam,
                parent=fam_parent,
                defaults={
                    'date_of_birth': date(2014 + random.randint(0,4), random.randint(1,12), random.randint(1,28)),
                    'gender': gender,
                    'color': random.choice(['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'])
                }
            )
            created_kids.append(kid)

        # Populate rosters
        for kid in created_kids:
            assigned = random.sample(teams, k=random.randint(1, 2))
            for team in assigned:
                membership, _ = TeamMembership.objects.get_or_create(
                    team=team,
                    user=kid.parent,
                    defaults={'role': 'parent'}
                )
                PlayerRegistration.objects.get_or_create(
                    team_membership=membership,
                    kid=kid,
                    defaults={'jersey_number': str(random.randint(1, 99))}
                )

        # Dashboard demo events: 7 per day for today + next 7 days (Pinnacle Performance)
        today = timezone.localdate()
        end_day = today + timedelta(days=7)
        TeamEvent.objects.filter(
            team__organization=org,
            start_time__date__gte=today,
            start_time__date__lte=end_day,
        ).delete()

        daily_slots = [
            ('Morning Practice', 'team', 8, 0, 'Main Gym'),
            ('Skills Session', 'training', 10, 0, 'Training Center'),
            ('Midday Scrimmage', 'team', 12, 0, 'Court A'),
            ('Agility Training', 'training', 14, 0, 'Fitness Lab'),
            ('Film Review', 'team', 16, 0, 'Team Room'),
            ('Shootaround', 'team', 18, 0, 'Court B'),
            ('Recovery Session', 'training', 19, 30, 'Wellness Studio'),
        ]
        demo_events_created = 0
        for day_offset in range(8):
            day = today + timedelta(days=day_offset)
            for idx, (label, event_type, hour, minute, location) in enumerate(daily_slots):
                team = teams[idx % len(teams)]
                start = timezone.make_aware(datetime.combine(day, time(hour, minute)))
                end = start + (timedelta(hours=1, minutes=30) if event_type == 'team' else timedelta(hours=1))
                event, created = TeamEvent.objects.get_or_create(
                    team=team,
                    start_time=start,
                    defaults={
                        'name': label,
                        'end_time': end,
                        'location': location,
                        'created_by': owner,
                        'description': (
                            f'[Dashboard Demo] {label} for {team.name} on '
                            f'{day.strftime("%A, %b %d")}.'
                        ),
                        'event_type': event_type,
                    },
                )
                if created:
                    demo_events_created += 1
                if event_type == 'training':
                    sample = random.sample(created_kids, min(6, len(created_kids)))
                    for kid in sample:
                        TeamEventInvitation.objects.get_or_create(
                            team_event=event,
                            user=kid.parent,
                            defaults={'status': random.choice(['pending', 'accepted'])},
                        )
                        TeamEventAttendance.objects.get_or_create(
                            team_event=event,
                            kid=kid,
                            defaults={'status': random.choice(['pending', 'accepted', 'accepted'])},
                        )
                else:
                    roster_kids = list(
                        Kid.objects.filter(
                            id__in=PlayerRegistration.objects.filter(
                                team_membership__team=team
                            ).values_list('kid_id', flat=True)
                        )[:8]
                    )
                    for kid in roster_kids:
                        TeamEventAttendance.objects.get_or_create(
                            team_event=event,
                            kid=kid,
                            defaults={'status': random.choice(['accepted', 'accepted', 'pending'])},
                        )
        self.stdout.write(f"Dashboard demo events: {demo_events_created} new ({8 * 7} slots across 8 days)")

        # One event always in progress now so "Happening Now" is visible on the dashboard
        now = timezone.now()
        live_team = teams[0]
        live_start = now - timedelta(minutes=20)
        live_end = now + timedelta(minutes=40)
        live_event, _ = TeamEvent.objects.update_or_create(
            team=live_team,
            name='Happening Now Demo',
            defaults={
                'start_time': live_start,
                'end_time': live_end,
                'location': 'Main Gym',
                'created_by': owner,
                'description': '[Dashboard Demo] Live event for Happening Now indicator.',
                'event_type': 'team',
            },
        )
        # Always refresh the window so the demo stays live on re-seed
        live_event.start_time = live_start
        live_event.end_time = live_end
        live_event.save(update_fields=['start_time', 'end_time'])

        # Personal events for darvin
        for i in range(2):
            start = timezone.now() + timedelta(days=random.randint(2, 12), hours=14)
            ev, _ = Event.objects.get_or_create(
                name=["Doctor Visit", "Birthday Party"][i % 2],
                family=darvin_kid1.family,
                start_time=start,
                end_time=start + timedelta(hours=2),
                location="Local",
                created_by=parent_user
            )
            ev.kids.add(darvin_kid1)

        # Ensure darvin kid on roster
        if teams:
            mem, _ = TeamMembership.objects.get_or_create(
                team=teams[0], user=parent_user, defaults={'role': 'parent'}
            )
            PlayerRegistration.objects.get_or_create(team_membership=mem, kid=darvin_kid1)

        self.stdout.write(self.style.SUCCESS(
            f"Seed complete! Owner has {Team.objects.filter(organization=org).count()} teams, "
            f"{PlayerRegistration.objects.count()} players, "
            f"{TeamEvent.objects.filter(event_type='team').count()} team events, "
            f"{TeamEvent.objects.filter(event_type='training').count()} trainings."
        ))
        self.stdout.write("Owner login: onefit / testpass123")
        self.stdout.write("Parent login: darvin / testpass123")
