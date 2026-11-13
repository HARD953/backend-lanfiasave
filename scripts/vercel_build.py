"""
Étape de build Vercel (déclarée dans pyproject.toml, [tool.vercel.scripts]).

- Vérifie la présence des variables indispensables pour échouer avec un message clair.
- Applique les migrations sur la base de production si DATABASE_URL est défini.
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
    missing = [v for v in ("DJANGO_SECRET_KEY", "DATABASE_URL") if not os.getenv(v)]
    if "DJANGO_SECRET_KEY" in missing:
        sys.exit("Variable DJANGO_SECRET_KEY manquante dans les Environment Variables du projet Vercel.")
    django.setup()
    if "DATABASE_URL" in missing:
        print("DATABASE_URL absent : migrations ignorées. Ajoutez une base PostgreSQL (Neon, Supabase…).")
        return
    print("Application des migrations…")
    call_command("migrate", interactive=False, verbosity=1)
    if os.getenv("SEED_DEMO", "").lower() in {"1", "true", "yes"}:
        print("SEED_DEMO actif : chargement du jeu de démonstration synthétique…")
        call_command("seed_demo")


if __name__ == "__main__":
    main()
