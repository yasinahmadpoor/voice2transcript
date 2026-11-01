from django import forms


class LoginForm(forms.Form):
    username = forms.CharField(min_length=1, max_length=150, strip=False)
    password = forms.CharField(min_length=1, max_length=1024, strip=False)
