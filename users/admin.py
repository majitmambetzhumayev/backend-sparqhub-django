# users/admin.py
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin

User = get_user_model()

# If someone else already registered User, unregister it first
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

@admin.register(User)
class CustomUserAdmin(DefaultUserAdmin):
    """Your customizations go here."""
    list_display = ("username", "email", "is_staff", "is_active")
    # You can override fieldsets, add search_fields, etc.
