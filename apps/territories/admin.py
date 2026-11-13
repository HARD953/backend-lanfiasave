from django.contrib import admin

from .models import Territory


@admin.register(Territory)
class TerritoryAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "level", "parent", "status"]
    list_filter = ["level", "status", "country"]
    search_fields = ["name", "code"]
