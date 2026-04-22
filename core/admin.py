from django.contrib import admin

# Register your models here.
from .models import Event, Family, Kid
admin.site.register(Event)
admin.site.register(Kid)
admin.site.register(Family)