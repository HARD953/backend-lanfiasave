from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "actor", "action", "outcome", "object_type", "object_id"]
    list_filter = ["outcome", "action"]
    search_fields = ["object_id", "action"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
