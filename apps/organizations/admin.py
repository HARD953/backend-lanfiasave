from django.contrib import admin

from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "kind", "country", "status"]
    search_fields = ["name", "code"]
