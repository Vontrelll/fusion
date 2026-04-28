from http.client import HTTPResponse
from django.shortcuts import render, redirect
from .models import Kid, Event
from django.http import HttpResponse 
from django.utils import timezone
from datetime import datetime
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required

# Create your views here.

@login_required
def dashboard(request):
    return render(request, "core/dashboard.html")

# Creating a new event. Connects to add_event.html
@login_required
def add_event(request):
    if request.method == "POST":
        name = request.POST.get("name")
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        location = request.POST.get("location")
        kid_id = request.POST.get('kid')
        kid = Kid.objects.get(id=kid_id) #Fetching the Kid

        #Converting strings to timezone-aware datetime
        start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
        end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        event = Event(name=name, start_time=start_time, end_time=end_time, 
        location=location, kid_attending=kid)
        
        #Conflict Detection 
        if has_conflict(event):
            return HttpResponse("Conflict detected! Event was not added")

        #No conflict --> event saved 
        event.save() 
        return HttpResponse("Event added successfully!")

    #GET request 
    kids = Kid.objects.all()
    return render(request, "core/add_event.html", {'kids': kids})
            

@login_required
def edit_event(request, event_id):
    event = Event.objects.get(id= event_id)
    if request.method == "POST":
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")
        start_time = timezone.make_aware(datetime.fromisoformat(start_time_str))
        end_time = timezone.make_aware(datetime.fromisoformat(end_time_str))
        event.name = request.POST.get("name")
        event.location = request.POST.get("location")
        event.start_time = start_time
        event.end_time = end_time

        kid_id = request.POST.get('kid')
        kid = Kid.objects.get(id=kid_id) 
        event.kid_attending = kid

        if has_conflict(event):
            return HttpResponse("Conflict detected! Event was not added")
        
        event.save()
        return redirect("event_list")


    kids = Kid.objects.all()
    return render(request, "core/edit_event.html", {"event": event,
    "kids": kids
    })

#Delete List
@login_required
def delete_event(request, event_id):
    event = Event.objects.get(id=event_id)
    if request.method == "POST":
        event.delete()
        return redirect("event_list")

    return render(request, "core/delete_event.html", {"event": event})



#Display list 
@login_required
def event_list(request):
    events = Event.objects.all().order_by('start_time')
    return render(request, "core/event_list.html", {'events': events})

#Conflict Detection 
def has_conflict(new_event):
    for event in Event.objects.all():
        if event.id == new_event.id:
            continue 
        if (new_event.start_time < event.end_time) and (new_event.end_time > event.start_time):
            return True

        return False 




#AUTHENTICATION 
def login_view(request):
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")
        user = authenticate(username=email, password=password)

        #This is not compelte code only a placeholder for the skeleton. would still 
        #need to redirect etc.
        if user:
            login(request, user)
            return redirect("dashboard")

        #This is not compelte code only a placeholder for the skeleton. I would 
        #need to setup real error messages re rendering etc.

        else:
            return render(request, "core/login.html", context={
                "error": "Invalid credentials, try again"
            })

    return render(request, "core/login.html")