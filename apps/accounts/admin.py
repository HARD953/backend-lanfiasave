from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Membership, User


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["organization"]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [MembershipInline]
    list_display = ["username", "first_name", "last_name", "is_active", "is_staff"]
    fieldsets = BaseUserAdmin.fieldsets + (("LanfiaSave", {"fields": ("phone", "must_change_password")}),)
