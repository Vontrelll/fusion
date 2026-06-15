from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from . models import Team, Organization, Profile
from django.utils import timezone  # for consent timestamp


# Shared timezone choices used for both signup (visible + auto-detected) and
# the account settings page. Keeps the list in one place and consistent.
TIMEZONE_CHOICES = [
    ('America/New_York', 'America/New_York (Eastern Time)'),
    ('America/Chicago', 'America/Chicago (Central Time)'),
    ('America/Denver', 'America/Denver (Mountain Time)'),
    ('America/Los_Angeles', 'America/Los_Angeles (Pacific Time)'),
    ('America/Phoenix', 'America/Phoenix (Arizona)'),
    ('America/Anchorage', 'America/Anchorage (Alaska)'),
    ('Pacific/Honolulu', 'Pacific/Honolulu (Hawaii)'),
    ('Europe/London', 'Europe/London'),
    ('Europe/Paris', 'Europe/Paris'),
    ('Europe/Berlin', 'Europe/Berlin'),
    ('Europe/Madrid', 'Europe/Madrid'),
    ('Asia/Tokyo', 'Asia/Tokyo'),
    ('Asia/Singapore', 'Asia/Singapore'),
    ('Asia/Kolkata', 'Asia/Kolkata (India)'),
    ('Australia/Sydney', 'Australia/Sydney'),
    ('UTC', 'UTC (Coordinated Universal Time)'),
]


class CustomUserCreationForm(UserCreationForm):
    first_name = forms.CharField(
        max_length=100, 
        required=True,
        widget=forms.TextInput(attrs={'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl'})
    )
    last_name = forms.CharField(
        max_length=100, 
        required=True,
        widget=forms.TextInput(attrs={'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl'})
    )

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500',
            'placeholder': 'you@example.com'
        }),
    )

    username = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500'
        })
    )
    
    role = forms.ChoiceField(
        choices=Profile.ROLE_CHOICES,
        widget=forms.Select(attrs={'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl'}),
        initial='parent',
        label="Choose your role"
    )

    # Privacy consent - required for COPPA/GDPR-style compliance with children's data
    consent = forms.BooleanField(
        required=True,
        label="",
        help_text="",
        widget=forms.CheckboxInput(attrs={
            'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500'
        })
    )

    # Timezone: visible select so users can see and correct the browser-detected value at signup.
    # JS on the signup page will try to pre-select the value from Intl.DateTimeFormat().
    # This replaces the old hidden field approach (which was opaque and could silently pick the wrong zone).
    timezone = forms.ChoiceField(
        choices=TIMEZONE_CHOICES,
        initial='America/Chicago',
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500'
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure password fields get the same nice input styling as first/last name
        if 'password1' in self.fields:
            self.fields['password1'].widget.attrs.update({
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500'
            })
        if 'password2' in self.fields:
            self.fields['password2'].widget.attrs.update({
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500'
            })

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'username', 'password1', 'password2']

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            # Case-insensitive check for existing email
            if User.objects.filter(email__iexact=email).exists():
                raise forms.ValidationError("An account with this email address already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['first_name'].strip().title() if self.cleaned_data.get('first_name') else ''
        user.last_name = self.cleaned_data['last_name'].strip().title() if self.cleaned_data.get('last_name') else ''
        user.email = self.cleaned_data['email']
        user.save()

        tz = self.cleaned_data.get('timezone') or 'America/Chicago'

        # Create Profile with chosen role + the timezone the user saw/corrected on the signup form
        # + record explicit consent timestamp (Step 3 privacy)
        profile = Profile.objects.create(
            user=user, 
            role=self.cleaned_data['role'],
            timezone=tz,
            data_consent_at=timezone.now()
        )
        
        return user


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ['name', 'sport_type', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
        }
        labels = {
            'name': 'Team Name',
            'sport_type': 'Sport Type',
            'description': 'Description (optional)',
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name:
            return name.strip().title()
        return name



class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500',
                'placeholder': 'e.g. Apex Athletics Club'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500',
                'rows': 4,
                'placeholder': 'Brief description of your organization (optional)'
            }),
        }
        labels = {
            'name': 'Organization Name',
            'description': 'Description',
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name:
            return name.strip().title()
        return name


from django import forms
from .models import TeamEvent

# core/forms.py
from django import forms
from .models import TeamEvent, Team

class TeamEventForm(forms.ModelForm):
    class Meta:
        model = TeamEvent
        fields = ['name', 'start_time', 'end_time', 'location', 'description', 'team']
        widgets = {
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name:
            return name.strip().title()
        return name

    def clean_location(self):
        location = self.cleaned_data.get('location')
        if location:
            return location.strip().title()
        return location

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        if self.user:
            self.fields['team'].queryset = Team.objects.filter(
                organization__owner=self.user
            )
        else:
            self.fields['team'].queryset = Team.objects.none()