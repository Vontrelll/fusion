from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import (
    Organization, Team, Profile, Family, Kid, TeamMembership,
    PlayerRegistration, TeamEvent, TeamEventAttendance, TeamEventInvitation,
    Event,
)
from django.utils import timezone
from datetime import timedelta, date, datetime, time
import random

# Demo window: ~2 weeks past + ~3 weeks ahead (re-seed refreshes this range)
PAST_DAYS = 14
FUTURE_DAYS = 21
LIVE_EVENT_NAME = 'Happening Now Demo'

TEAM_PROFILES = [
    (
        'U8 Boys Firehawks',
        'basketball',
        '#dc2626',
        'Rookie basketball for 7–8 year olds. Focus on dribbling, spacing, and game IQ.',
    ),
    (
        'U8 Girls Lightning',
        'basketball',
        '#7c3aed',
        'High-energy intro league for young athletes building confidence on the court.',
    ),
    (
        'U10 Boys Strikers',
        'soccer',
        '#059669',
        'Competitive U10 soccer with emphasis on passing patterns and field awareness.',
    ),
    (
        'U10 Girls Storm',
        'soccer',
        '#0891b2',
        'Technical training and small-sided matches for developing midfielders and forwards.',
    ),
    (
        'U12 Boys Titans',
        'football',
        '#b45309',
        'Flag-to-tackle progression team preparing for regional youth football league play.',
    ),
    (
        'U12 Girls Eagles',
        'football',
        '#4f46e5',
        'Skills, conditioning, and teamwork for athletes new to organized football.',
    ),
    (
        'U14 Boys Premier',
        'basketball',
        '#1d4ed8',
        'Travel-level training with film review, set plays, and tournament scheduling.',
    ),
]

# Legacy short names → new identity (avoids duplicate teams on re-seed)
LEGACY_TEAM_NAMES = {
    'U8 Boys': 'U8 Boys Firehawks',
    'U8 Girls': 'U8 Girls Lightning',
    'U10 Boys': 'U10 Boys Strikers',
    'U10 Girls': 'U10 Girls Storm',
    'U12 Boys': 'U12 Boys Titans',
    'U12 Girls': 'U12 Girls Eagles',
    'U14 Boys': 'U14 Boys Premier',
}

DEMO_FAMILIES = [
    ('Marcus', 'Reed', 'Jordan', 'M', 2016, 3, 14),
    ('Tanya', 'Reed', 'Aaliyah', 'F', 2015, 7, 8),
    ('Brian', 'Nguyen', 'Ethan', 'M', 2014, 11, 2),
    ('Priya', 'Nguyen', 'Anika', 'F', 2016, 5, 19),
    ('Carlos', 'Mendoza', 'Mateo', 'M', 2015, 9, 30),
    ('Elena', 'Mendoza', 'Sofia', 'F', 2017, 1, 11),
    ('James', 'Whitaker', 'Caleb', 'M', 2014, 4, 25),
    ('Rachel', 'Whitaker', 'Nora', 'F', 2016, 8, 6),
    ('David', 'Patel', 'Arjun', 'M', 2015, 12, 1),
    ('Meera', 'Patel', 'Diya', 'F', 2017, 6, 17),
    ('Anthony', 'Brooks', 'Malik', 'M', 2014, 2, 9),
    ('Keisha', 'Brooks', 'Zoe', 'F', 2016, 10, 22),
    ('Tom', 'Fischer', 'Logan', 'M', 2015, 3, 3),
    ('Anna', 'Fischer', 'Hannah', 'F', 2017, 7, 15),
    ('Chris', 'Okafor', 'Emeka', 'M', 2014, 9, 28),
    ('Amara', 'Okafor', 'Chioma', 'F', 2016, 1, 4),
    ('Ryan', 'Sullivan', 'Connor', 'M', 2015, 5, 31),
    ('Molly', 'Sullivan', 'Grace', 'F', 2017, 11, 12),
    ('Andre', 'Washington', 'Darius', 'M', 2014, 8, 18),
    ('Jasmine', 'Washington', 'Layla', 'F', 2016, 4, 7),
    ('Kevin', 'Park', 'Min-jun', 'M', 2015, 6, 23),
    ('Hannah', 'Park', 'Soo-yeon', 'F', 2017, 2, 14),
]

VENUES = [
    'Riverside Sports Complex — Court 1',
    'Riverside Sports Complex — Court 2',
    'Northside Turf Field',
    'South Campus Training Center',
    'Pinnacle Performance Gym',
    'Wellness Studio',
    'Memorial Park Field 3',
]

SPORT_EVENT_NAMES = {
    'basketball': [
        ('Shootaround', 'team'),
        ('Team Practice', 'team'),
        ('Scrimmage Night', 'team'),
        ('League Game vs Metro Hawks', 'team'),
        ('Free-Throw Clinic', 'training'),
        ('Ball Handling Lab', 'training'),
        ('Film & Strategy Session', 'team'),
    ],
    'soccer': [
        ('Passing Pattern Practice', 'team'),
        ('Small-Sided Scrimmage', 'team'),
        ('Match vs Eastside FC', 'team'),
        ('Finishing Clinic', 'training'),
        ('Possession Training', 'training'),
        ('Saturday League Match', 'team'),
    ],
    'football': [
        ('Team Practice', 'team'),
        ('7-on-7 Scrimmage', 'team'),
        ('Conditioning & Agility', 'training'),
        ('Game vs Westview Eagles', 'team'),
        ('Route Running Session', 'training'),
        ('Friday Night Prep', 'team'),
    ],
}

DARVIN_PERSONAL_EVENTS = [
    ('Liam — Orthodontist Checkup', 'Maple Street Dental', 3, 15, 45, [0]),
    ('Mia — Piano Recital', 'Lincoln Arts Center', 8, 18, 0, [1]),
    ('Family — Grandparents Visit', 'Home', 11, 11, 0, [0, 1]),
    ('Liam — School Picture Day', 'Oakwood Elementary', 5, 8, 30, [0]),
    ('Vontrell Family BBQ', 'Backyard', 14, 16, 0, [0, 1]),
    ('Mia — Dentist Cleaning', 'Maple Street Dental', 18, 9, 0, [1]),
]


class Command(BaseCommand):
    help = 'Seeds comprehensive test data for demo purposes (owner: onefit, parent: darvin)'

    def handle(self, *args, **options):
        random.seed(42)
        self.stdout.write('Starting test data seed...')

        try:
            owner = User.objects.get(username='onefit')
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR('onefit user not found. Please ensure it exists.'))
            return

        if not owner.first_name:
            owner.first_name = 'Jordan'
            owner.last_name = 'Mercer'
            owner.save(update_fields=['first_name', 'last_name'])

        try:
            org = Organization.objects.get(owner=owner)
        except Organization.DoesNotExist:
            org = Organization.objects.create(
                name='Pinnacle Performance Academy',
                owner=owner,
            )

        org.name = 'Pinnacle Performance Academy'
        org.description = (
            'Premier youth sports club serving families across the metro area. '
            'Basketball, soccer, and football programs from U8 through U14 with '
            'trained coaches, structured seasons, and tournament travel teams.'
        )
        org.save(update_fields=['name', 'description'])
        self.stdout.write(f'Using org: {org.name}')

        teams = self._ensure_teams(org)
        parent_user, darvin_kid1, darvin_kid2 = self._ensure_darvin_family()
        created_kids = self._ensure_demo_families(teams, darvin_kid1, darvin_kid2)
        self._assign_darvin_rosters(parent_user, darvin_kid1, darvin_kid2, teams)
        self._seed_schedule(org, owner, teams, created_kids)
        self._seed_darvin_personal_events(parent_user, darvin_kid1, darvin_kid2)
        self._ensure_darvin_team_attendance(darvin_kid1, darvin_kid2)

        team_count = Team.objects.filter(organization=org).count()
        player_count = PlayerRegistration.objects.filter(
            team_membership__team__organization=org
        ).count()
        team_events = TeamEvent.objects.filter(team__organization=org, event_type='team').count()
        trainings = TeamEvent.objects.filter(team__organization=org, event_type='training').count()

        self.stdout.write(self.style.SUCCESS(
            f'Seed complete! {team_count} teams, {player_count} roster entries, '
            f'{team_events} team events, {trainings} training sessions.'
        ))
        self.stdout.write('Owner login: onefit / (your existing password)')
        self.stdout.write('Parent login: darvin / (your existing password)')

    def _ensure_teams(self, org):
        teams = []
        for name, sport, color, description in TEAM_PROFILES:
            team = Team.objects.filter(organization=org, name=name).first()
            if not team:
                for old_name, new_name in LEGACY_TEAM_NAMES.items():
                    if new_name == name:
                        legacy = Team.objects.filter(organization=org, name=old_name).first()
                        if legacy:
                            legacy.name = name
                            legacy.save(update_fields=['name'])
                            team = legacy
                            break
            if not team:
                team = Team.objects.create(
                    name=name,
                    organization=org,
                    sport_type=sport,
                    color=color,
                    description=description,
                )
                self.stdout.write(f'Created team: {name}')
            else:
                team.sport_type = sport
                team.color = color
                team.description = description
                team.save(update_fields=['sport_type', 'color', 'description'])
            teams.append(team)
        return teams

    def _ensure_darvin_family(self):
        try:
            parent_user = User.objects.get(username='darvin')
        except User.DoesNotExist:
            parent_user = User.objects.create_user(
                'darvin', 'darvinvontrell@gmail.com', 'testpass123',
            )

        parent_user.first_name = 'Darvin'
        parent_user.last_name = 'Vontrell'
        parent_user.save(update_fields=['first_name', 'last_name'])

        parent_profile, _ = Profile.objects.get_or_create(
            user=parent_user, defaults={'role': 'parent'},
        )
        if parent_profile.role != 'parent':
            parent_profile.role = 'parent'
            parent_profile.save(update_fields=['role'])

        family, _ = Family.objects.get_or_create(
            family_name='Vontrell Family',
            defaults={'created_by': parent_user},
        )
        parent_profile.family = family
        parent_profile.save(update_fields=['family'])

        darvin_kid1, _ = Kid.objects.update_or_create(
            first_name='Liam',
            last_name='Vontrell',
            family=family,
            parent=parent_user,
            defaults={
                'date_of_birth': date(2015, 5, 12),
                'gender': 'M',
                'color': '#2563eb',
            },
        )
        darvin_kid2, _ = Kid.objects.update_or_create(
            first_name='Mia',
            last_name='Vontrell',
            family=family,
            parent=parent_user,
            defaults={
                'date_of_birth': date(2017, 8, 22),
                'gender': 'F',
                'color': '#db2777',
            },
        )
        return parent_user, darvin_kid1, darvin_kid2

    def _ensure_demo_families(self, teams, darvin_kid1, darvin_kid2):
        created_kids = [darvin_kid1, darvin_kid2]
        kid_colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4']

        for idx, row in enumerate(DEMO_FAMILIES):
            parent_first, parent_last, kid_first, gender, birth_year, birth_month, birth_day = row
            parent_username = f'parentdemo{idx + 1}'

            try:
                fam_parent = User.objects.get(username=parent_username)
            except User.DoesNotExist:
                fam_parent = User.objects.create_user(
                    parent_username,
                    f'{parent_last.lower()}.{parent_first.lower()}@pinnacle.demo',
                    'testpass123',
                )

            fam_parent.first_name = parent_first
            fam_parent.last_name = parent_last
            fam_parent.save(update_fields=['first_name', 'last_name'])

            fam_profile, _ = Profile.objects.get_or_create(
                user=fam_parent, defaults={'role': 'parent'},
            )
            fam, _ = Family.objects.get_or_create(
                family_name=f'{parent_last} Family',
                defaults={'created_by': fam_parent},
            )
            fam_profile.family = fam
            fam_profile.save(update_fields=['family'])

            kid, _ = Kid.objects.update_or_create(
                first_name=kid_first,
                last_name=parent_last,
                family=fam,
                parent=fam_parent,
                defaults={
                    'date_of_birth': date(birth_year, birth_month, birth_day),
                    'gender': gender,
                    'color': kid_colors[idx % len(kid_colors)],
                },
            )
            created_kids.append(kid)

        for kid in created_kids:
            if kid in (darvin_kid1, darvin_kid2):
                continue
            assigned = random.sample(teams, k=random.randint(1, 2))
            for team in assigned:
                membership, _ = TeamMembership.objects.get_or_create(
                    team=team,
                    user=kid.parent,
                    defaults={'role': 'parent'},
                )
                PlayerRegistration.objects.get_or_create(
                    team_membership=membership,
                    kid=kid,
                    defaults={'jersey_number': str(random.randint(1, 99))},
                )

        return created_kids

    def _assign_darvin_rosters(self, parent_user, liam, mia, teams):
        team_by_name = {t.name: t for t in teams}
        assignments = [
            (liam, 'U10 Boys Strikers', '14'),
            (mia, 'U8 Girls Lightning', '7'),
        ]
        for kid, team_name, jersey in assignments:
            team = team_by_name.get(team_name)
            if not team:
                continue
            membership, _ = TeamMembership.objects.get_or_create(
                team=team,
                user=parent_user,
                defaults={'role': 'parent'},
            )
            PlayerRegistration.objects.update_or_create(
                team_membership=membership,
                kid=kid,
                defaults={'jersey_number': jersey},
            )

    def _seed_schedule(self, org, owner, teams, created_kids):
        today = timezone.localdate()
        range_start = today - timedelta(days=PAST_DAYS)
        range_end = today + timedelta(days=FUTURE_DAYS)

        TeamEvent.objects.filter(
            team__organization=org,
            start_time__date__gte=range_start,
            start_time__date__lte=range_end,
        ).exclude(name=LIVE_EVENT_NAME).delete()

        created_count = 0
        team_cycle = 0
        roster_cache = {}

        for day_offset in range(-PAST_DAYS, FUTURE_DAYS + 1):
            day = today + timedelta(days=day_offset)
            weekday = day.weekday()
            slots = self._slots_for_day(weekday, day_offset)

            for slot_idx, (hour, minute) in enumerate(slots):
                team = teams[team_cycle % len(teams)]
                team_cycle += 1
                label, event_type = self._pick_event_name(team)
                location = VENUES[(day_offset + slot_idx) % len(VENUES)]
                start = timezone.make_aware(datetime.combine(day, time(hour, minute)))
                duration = timedelta(hours=1, minutes=30) if event_type == 'team' else timedelta(hours=1)
                end = start + duration

                event, created = TeamEvent.objects.get_or_create(
                    team=team,
                    start_time=start,
                    defaults={
                        'name': label,
                        'end_time': end,
                        'location': location,
                        'created_by': owner,
                        'description': (
                            f'{label} for {team.name}. '
                            f'{day.strftime("%A, %B %d")} at {location}.'
                        ),
                        'event_type': event_type,
                    },
                )
                if created:
                    created_count += 1

                self._populate_attendance(event, event_type, team, created_kids, roster_cache)

        self._ensure_live_event(owner, teams[0])
        total_slots = sum(
            len(self._slots_for_day((today + timedelta(days=d)).weekday(), d))
            for d in range(-PAST_DAYS, FUTURE_DAYS + 1)
        )
        self.stdout.write(
            f'Schedule: {created_count} new events across {PAST_DAYS} past + {FUTURE_DAYS} future days '
            f'(~{total_slots} slots).'
        )

    def _slots_for_day(self, weekday, day_offset):
        """Weekday = 0 Mon … 6 Sun. Fewer slots on weekends; busier mid-week."""
        if weekday == 6:
            return [(9, 0), (11, 0), (14, 0)]
        if weekday == 5:
            return [(8, 30), (10, 0), (12, 0), (15, 0)]
        if weekday in (1, 3):
            return [(16, 0), (17, 30), (18, 45), (19, 30)]
        return [(17, 0), (18, 15), (19, 0)]

    def _pick_event_name(self, team):
        pool = SPORT_EVENT_NAMES.get(team.sport_type, SPORT_EVENT_NAMES['basketball'])
        return random.choice(pool)

    def _populate_attendance(self, event, event_type, team, created_kids, roster_cache):
        if event_type == 'training':
            sample = random.sample(created_kids, min(6, len(created_kids)))
            for kid in sample:
                TeamEventInvitation.objects.get_or_create(
                    team_event=event,
                    user=kid.parent,
                    defaults={'status': random.choice(['pending', 'accepted', 'accepted'])},
                )
                TeamEventAttendance.objects.get_or_create(
                    team_event=event,
                    kid=kid,
                    defaults={'status': random.choice(['pending', 'accepted', 'accepted'])},
                )
        else:
            if team.id not in roster_cache:
                roster_cache[team.id] = list(
                    Kid.objects.filter(
                        id__in=PlayerRegistration.objects.filter(
                            team_membership__team=team,
                        ).values_list('kid_id', flat=True),
                    )[:10]
                )
            for kid in roster_cache[team.id]:
                TeamEventAttendance.objects.get_or_create(
                    team_event=event,
                    kid=kid,
                    defaults={'status': random.choice(['accepted', 'accepted', 'pending'])},
                )

    def _ensure_live_event(self, owner, team):
        now = timezone.now()
        live_start = now - timedelta(minutes=20)
        live_end = now + timedelta(minutes=40)
        live_event, _ = TeamEvent.objects.update_or_create(
            team=team,
            name=LIVE_EVENT_NAME,
            defaults={
                'start_time': live_start,
                'end_time': live_end,
                'location': 'Pinnacle Performance Gym',
                'created_by': owner,
                'description': 'Live scrimmage block — demo event for the Happening Now indicator.',
                'event_type': 'team',
            },
        )
        live_event.start_time = live_start
        live_event.end_time = live_end
        live_event.save(update_fields=['start_time', 'end_time'])

    def _seed_darvin_personal_events(self, parent_user, liam, mia):
        kids = [liam, mia]
        for name, location, day_offset, hour, minute, kid_indexes in DARVIN_PERSONAL_EVENTS:
            day = timezone.localdate() + timedelta(days=day_offset)
            start = timezone.make_aware(datetime.combine(day, time(hour, minute)))
            end = start + timedelta(hours=1 if 'Checkup' in name or 'Dentist' in name else 2)
            ev, _ = Event.objects.update_or_create(
                name=name,
                family=liam.family,
                start_time=start,
                defaults={
                    'end_time': end,
                    'location': location,
                    'created_by': parent_user,
                    'description': f'Family calendar — {name}',
                },
            )
            ev.kids.set([kids[i] for i in kid_indexes])

    def _ensure_darvin_team_attendance(self, liam, mia):
        """Parent calendar only shows accepted team events for family kids."""
        today = timezone.localdate()
        range_start = timezone.make_aware(datetime.combine(today - timedelta(days=PAST_DAYS), time.min))
        range_end = timezone.make_aware(datetime.combine(today + timedelta(days=FUTURE_DAYS), time.max))

        for kid in (liam, mia):
            team_ids = PlayerRegistration.objects.filter(kid=kid).values_list(
                'team_membership__team_id', flat=True,
            )
            event_ids = TeamEvent.objects.filter(
                team_id__in=team_ids,
                start_time__gte=range_start,
                start_time__lte=range_end,
            ).values_list('id', flat=True)
            for event_id in event_ids:
                TeamEventAttendance.objects.update_or_create(
                    team_event_id=event_id,
                    kid=kid,
                    defaults={'status': 'accepted'},
                )