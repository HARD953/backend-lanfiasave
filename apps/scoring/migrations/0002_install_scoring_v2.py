"""Installe les grilles de scoring : v1 (désactivée, conservée pour relire l'historique) et v2 (active)."""
from django.db import migrations


def install(apps, schema_editor):
    from apps.scoring.engine import SCORING_V1, SCORING_V2

    S = apps.get_model("scoring", "ScoringModelVersion")
    S.objects.get_or_create(code="VULN", version=1, defaults={"config": SCORING_V1, "is_active": False,
                                                                "notes": "Grille de référence SRC-03."})
    S.objects.filter(code="VULN", version=1).update(is_active=False)
    S.objects.get_or_create(code="VULN", version=2, defaults={
        "config": SCORING_V2, "is_active": True,
        "notes": "Grille v2 : alimentation, logement, chocs, enfance. Seuils non calibrés scientifiquement."})
    S.objects.filter(code="VULN", version=2).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [("scoring", "0001_initial")]
    operations = [migrations.RunPython(install, migrations.RunPython.noop)]
