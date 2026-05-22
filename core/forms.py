from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from . models import Team, Organization, Profile

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
    
    role = forms.ChoiceField(
        choices=Profile.ROLE_CHOICES,
        widget=forms.Select(attrs={'class': 'w-full px-4 py-3 border border-gray-300 rounded-2xl'}),
        initial='parent',
        label="Choose your role"
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'password1', 'password2', 'role']

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.save()

        # Create Profile with chosen role
        Profile.objects.create(
            user=user, 
            role=self.cleaned_data['role']
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