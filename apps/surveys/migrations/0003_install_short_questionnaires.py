"""Installe les formulaires courts : Express (urgence) et Standard, sous-ensembles du questionnaire complet v2."""
from django.db import migrations
from django.utils import timezone


def install(apps, schema_editor):
    from apps.surveys.definitions import QUESTIONNAIRE_EXPRESS, QUESTIONNAIRE_STANDARD

    Q = apps.get_model("surveys", "QuestionnaireVersion")
    for d in (QUESTIONNAIRE_EXPRESS, QUESTIONNAIRE_STANDARD):
        Q.objects.get_or_create(code=d["code"], version=d["version"], defaults={
            "title": d["title"], "definition": d, "status": "published", "published_at": timezone.now()})


class Migration(migrations.Migration):
    dependencies = [("surveys", "0002_install_questionnaires")]
    operations = [migrations.RunPython(install, migrations.RunPython.noop)]
