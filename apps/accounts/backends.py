"""Use Django accounts with one rate limit for frontend and admin sign-in."""

from django.contrib.auth.backends import ModelBackend

from apps.accounts.services import check_login_rate


class RateLimitedModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if request is not None:
            check_login_rate(request)
        return super().authenticate(
            request, username=username, password=password, **kwargs
        )
