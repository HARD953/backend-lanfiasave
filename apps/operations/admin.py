from django.contrib import admin

from .models import Operation, Partner


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ["name", "acronym", "kind", "organization", "is_active"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "acronym"]


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ["code", "title", "kind", "status", "organization", "start_date"]
    list_filter = ["kind", "status"]
    search_fields = ["code", "title", "sites"]
