"""
Étape de build Vercel (déclarée dans pyproject.toml, [tool.vercel.scripts]).

- Vérifie la présence des variables indispensables pour échouer avec un message clair.
- Exige une base PostgreSQL (DATABASE_URL ou variante préfixée) et y applique les migrations.
  (collectstatic est lancé automatiquement par Vercel.)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402
from django.core.management import call_command  # noqa: E402


def main():
    if not os.getenv("DJANGO_SECRET_KEY"):
        sys.exit("Variable DJANGO_SECRET_KEY manquante dans les Environment Variables du projet Vercel.")
    try:
        django.setup()  # échoue avec un message clair si aucune base n'est configurée sur Vercel
    except RuntimeError as exc:
        sys.exit(str(exc))
    from django.conf import settings

    print(f"Base de données lue depuis la variable : {settings.DATABASE_URL_SOURCE}")
    print("Application des migrations…")
    call_command("migrate", interactive=False, verbosity=1)
    if os.getenv("SEED_DEMO", "").lower() in {"1", "true", "yes"}:
        print("SEED_DEMO actif : chargement du jeu de démonstration synthétique…")
        call_command("seed_demo")


if __name__ == "__main__":
    main()
