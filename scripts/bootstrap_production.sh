#!/usr/bin/env bash
# Crée le premier administrateur sur la base de PRODUCTION, depuis votre poste.
# L'URL de la base et le mot de passe sont saisis de façon masquée : rien n'est affiché ni enregistré.
#
#   ./scripts/bootstrap_production.sh
#
# L'URL se trouve dans Vercel → projet backend → Settings → Environment Variables → DATABASE_URL
# (ou dans Neon → Connection string).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

printf "Collez l'URL DATABASE_URL de production (saisie masquée) : "
IFS= read -rs DATABASE_URL
echo
[ -n "$DATABASE_URL" ] || { echo "URL vide, abandon."; exit 1; }

# Clé temporaire : la commande n'émet ni jeton ni cookie, elle écrit seulement en base.
DJANGO_SECRET_KEY="bootstrap-$(openssl rand -hex 32)" DJANGO_DEBUG=false DATABASE_URL="$DATABASE_URL" \
  "$PYTHON" manage.py bootstrap_admin "$@"
