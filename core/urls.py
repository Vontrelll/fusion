from django.urls import path
from . import views


urlpatterns = [
    path('add_event/', views.add_event, name = 'add_event'),
    path('edit_event/<int:event_id>/', views.edit_event, name = 'edit_event'),
    path('', views.event_list, name='event_list'),
    path('delete_event/<int:event_id>/', views.delete_event, name= 'delete_event'),
    path('login/', views.login_view, name='login'),
    path('dashboard', views.dashboard, name='dashboard')
]