from django.urls import path

from . import views

urlpatterns = [
    path("login", views.login),
    path("me", views.current_user),
    path("logout", views.logout),
]
urlpatterns += [path(f"{route.pattern}/", route.callback) for route in urlpatterns]
