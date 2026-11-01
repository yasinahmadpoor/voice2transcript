from django.urls import path

from . import schema, views

urlpatterns = [
    path("", views.frontend),
    path("health", views.health),
    path("transcription/defaults", views.defaults),
    path("jobs", views.jobs),
    path("jobs/<str:job_id>", views.job_detail),
    path("jobs/<str:job_id>/result", views.job_result),
    path("jobs/<str:job_id>/retry", views.retry_job),
    path("transcribe", views.transcribe),
    path("openapi.json", schema.openapi),
    path("docs", schema.docs),
]
# Accept trailing slashes without redirecting an upload or changing its method.
urlpatterns += [
    path(f"{route.pattern}/", route.callback)
    for route in urlpatterns
    if str(route.pattern)
]
