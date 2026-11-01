from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.transcriptions.urls")),
]

handler400 = "common.http.bad_request"
handler404 = "common.http.not_found"
handler500 = "common.http.server_error"
