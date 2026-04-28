from django.db import models
from django.conf import settings

# Create your models here.
GENDER_CHOICES = [("M", "Male"), ("F", "Female")]

class Event(models.Model):
    name = models.CharField(max_length=100)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length = 100)
    kid_attending = models.ForeignKey("Kid", on_delete=models.CASCADE, null=True, blank=True, related_name = 'events')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} for {self.kid_attending} at {self.start_time}"



    def __str__(self):
        return f"{self.name} for {self.kid_attending} at {self.start_time}"


class Kid(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(choices = GENDER_CHOICES, max_length=1)
    family = models.ForeignKey("Family", on_delete=models.CASCADE, related_name = 'kids')
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

class Family(models.Model):
    family_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    parents = models.ManyToManyField(settings.AUTH_USER_MODEL)

    def __str__(self):
        return self.family_name
