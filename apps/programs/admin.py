from django.contrib import admin

from .models import Program


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "sector", "status"]
    list_filter = ["status", "sector"]
