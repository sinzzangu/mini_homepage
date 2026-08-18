from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.calendar_page, name="calendar"),
    path("feed.ics", views.feed, name="feed"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="calendars/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("settings/", views.settings_page, name="settings"),
    path("api/events", views.events_collection, name="events-collection"),
    path("api/events/<int:pk>", views.event_detail, name="event-detail"),
]
