from django.contrib import admin

from .models import QuestionnaireVersion


@admin.register(QuestionnaireVersion)
class QuestionnaireVersionAdmin(admin.ModelAdmin):
    list_display = ["code", "version", "title", "status", "published_at"]
