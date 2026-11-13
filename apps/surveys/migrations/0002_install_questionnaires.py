"""Installe les questionnaires de référence : v1 (retiré, conservé pour l'historique) et v2 (publié)."""
from django.db import migrations
from django.utils import timezone


def install(apps, schema_editor):
    from apps.surveys.definitions import QUESTIONNAIRE_V1, QUESTIONNAIRE_V2

    Q = apps.get_model("surveys", "QuestionnaireVersion")
    for definition, status in ((QUESTIONNAIRE_V1, "retired"), (QUESTIONNAIRE_V2, "published")):
        obj, created = Q.objects.get_or_create(
            code=definition["code"], version=definition["version"],
            defaults={"title": definition["title"], "definition": definition, "status": status,
                      "published_at": timezone.now()})
        if not created and obj.status != status and definition["version"] == 1:
            Q.objects.filter(pk=obj.pk).update(status=status)


class Migration(migrations.Migration):
    dependencies = [("surveys", "0001_initial")]
    operations = [migrations.RunPython(install, migrations.RunPython.noop)]
