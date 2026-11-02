"""Inspect jobs and use queue operations without editing live worker state."""

from datetime import UTC, datetime
from threading import Lock

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from apps.transcriptions.services.queue import JobConflict, JobNotFound, QueueFull

from .models import TranscriptionJob
from .models import ModelProvider
from .forms import ModelProviderAdminForm
from .services.runtime import get_queue
from .services.connection_test import check_connection

_connection_test_lock = Lock()

admin.site.site_header = "Voice to Transcript administration"
admin.site.site_title = "Voice to Transcript"
admin.site.index_title = "Administration"


@admin.register(ModelProvider)
class ModelProviderAdmin(admin.ModelAdmin):
    form = ModelProviderAdminForm
    change_form_template = "admin/transcriptions/modelprovider/change_form.html"
    list_display = ["name", "kind", "model", "is_enabled", "is_default", "has_api_key"]
    readonly_fields = ["is_default", "has_api_key"]
    fields = [
        "name",
        "kind",
        "base_url",
        "api_key",
        "clear_api_key",
        "has_api_key",
        "model",
        "fallback_model",
        "is_enabled",
        "is_default",
    ]
    actions = ["use_as_default", "use_environment_default"]

    def get_urls(self):
        return [
            path(
                "<int:object_id>/test-connection/",
                self.admin_site.admin_view(self.test_connection),
                name="web_modelprovider_test_connection",
            ),
        ] + super().get_urls()

    @method_decorator(require_POST)
    def test_connection(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        provider = self.get_object(request, str(object_id))
        if provider is None:
            raise Http404
        status = 200
        if not _connection_test_lock.acquire(blocking=False):
            result = {
                "ok": False,
                "message": "Another model connection test is running. Try again shortly.",
            }
            status = 429
        else:
            try:
                result = check_connection(provider)
            finally:
                _connection_test_lock.release()
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(result, status=status)
        self.message_user(
            request,
            result["message"],
            messages.SUCCESS if result["ok"] else messages.ERROR,
        )
        return redirect(reverse("admin:web_modelprovider_change", args=[provider.pk]))

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(boolean=True, description="API key saved")
    def has_api_key(self, obj):
        return bool(obj.api_key_encrypted)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if (
            not change
            and obj.is_enabled
            and not ModelProvider.objects.filter(is_default=True).exists()
        ):
            obj.make_default()

    @admin.action(description="Use selected provider as the default")
    def use_as_default(self, request, queryset):
        if queryset.count() != 1 or not queryset.first().is_enabled:
            self.message_user(
                request, "Select exactly one enabled provider.", messages.ERROR
            )
            return
        queryset.first().make_default()
        self.message_user(request, "Default provider updated for new transcriptions.")

    @admin.action(description="Restore environment Gemini as the default")
    def use_environment_default(self, request, queryset):
        ModelProvider.objects.filter(is_default=True).update(is_default=False)
        self.message_user(request, "New transcriptions default to environment Gemini.")


@admin.register(TranscriptionJob)
class TranscriptionJobAdmin(admin.ModelAdmin):
    list_display = ["filename", "owner_name", "status", "created", "interrupted"]
    list_filter = ["status", "interrupted"]
    search_fields = ["id", "filename", "owner"]
    readonly_fields = [field.name for field in TranscriptionJob._meta.fields]
    actions = [
        "retry_transcriptions",
        "restart_transcriptions",
        "cancel_transcriptions",
        "remove_recordings",
    ]

    @admin.display(description="Owner", ordering="owner")
    def owner_name(self, job):
        if job.owner.startswith("user:"):
            user = get_user_model().objects.filter(pk=job.owner[5:]).first()
            return user.get_username() if user else f"Deleted account ({job.owner})"
        return job.owner

    def get_search_results(self, request, queryset, search_term):
        results, duplicates = super().get_search_results(request, queryset, search_term)
        ids = (
            get_user_model()
            .objects.filter(username__icontains=search_term)
            .values_list("pk", flat=True)
        )
        return queryset.filter(
            Q(pk__in=results.values("pk")) | Q(owner__in=[f"user:{pk}" for pk in ids])
        ), duplicates

    @admin.display(description="Created", ordering="created_at")
    def created(self, job):
        return datetime.fromtimestamp(job.created_at, UTC)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(
        description="Resume or retry selected transcriptions", permissions=["retry"]
    )
    def retry_transcriptions(self, request, queryset):
        self.queue_action(request, queryset, "retry")

    def has_retry_permission(self, request):
        return request.user.is_superuser

    @admin.action(
        description="Restart selected transcriptions from beginning",
        permissions=["retry"],
    )
    def restart_transcriptions(self, request, queryset):
        self.queue_action(request, queryset, "retry", restart=True)

    @admin.action(description="Cancel selected transcriptions", permissions=["cancel"])
    def cancel_transcriptions(self, request, queryset):
        self.queue_action(request, queryset, "cancel")

    def has_cancel_permission(self, request):
        return request.user.is_superuser

    @admin.action(
        description="Remove selected recordings and results", permissions=["remove"]
    )
    def remove_recordings(self, request, queryset):
        self.queue_action(request, queryset, "delete")

    def has_remove_permission(self, request):
        return request.user.is_superuser

    def queue_action(self, request, queryset, operation, **options):
        queue = get_queue()
        succeeded = 0
        for job in queryset:
            try:
                getattr(queue, operation)(job.id, job.owner, **options)
                succeeded += 1
            except (JobConflict, JobNotFound, QueueFull) as exc:
                self.message_user(request, f"{job.filename}: {exc}", messages.WARNING)
        if succeeded:
            self.message_user(
                request, f"{succeeded} recording(s) updated.", messages.SUCCESS
            )
