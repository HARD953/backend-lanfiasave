# LanfiaSave — API (backend)

API REST v1 de la plateforme LanfiaSave 2.1 (cahier des charges LFS-CDC-2026-2.1) : protection sociale,
formation et inclusion économique des personnes vulnérables.

**Pile** : Python 3.13+, Django 6, Django REST Framework, PostgreSQL, JWT (SimpleJWT), OpenAPI (drf-spectacular).

Dépôts associés : `web-lanfiasave` (portail Next.js) et `mobile-lanfiasave` (applications Expo Collecte et Mon Parcours).

## Démarrage

```bash
cp .env.example .env              # remplacer chaque valeur « remplacer »
docker compose up -d db           # PostgreSQL isolé sur 127.0.0.1:5433
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo          # jeu de démonstration 100 % synthétique
DJANGO_DEBUG=true .venv/bin/python manage.py runserver 0.0.0.0:8000
```

- Documentation interactive : <http://localhost:8000/api/docs/>
- Santé : <http://localhost:8000/health/>
- Tout en Docker : `docker compose up --build`

## Modules

| Module | Contenu |
|---|---|
| `core` | Identifiants UUID, journal d'audit en ajout seul, idempotence, format d'erreur, indicateurs, espace bénéficiaire |
| `accounts` | Utilisateurs, rôles métier, adhésions par organisation/programme, connexion staff et bénéficiaire |
| `organizations` | Organisations clientes (unité d'isolation des données) |
| `territories` | Référentiel territorial hiérarchique daté et sourcé |
| `beneficiaries` | Personnes, ménages, appartenance datée, doublons, fusion réversible, masquage des champs sensibles |
| `surveys` | Questionnaire versionné en 12 rubriques, synchronisation terrain idempotente |
| `scoring` | Score de vulnérabilité à 7 dimensions, consultatif, explicable, versionné |
| `programs` | Programmes, cycle de vie, règles d'éligibilité versionnées |
| `cases` | Candidatures, instruction, décisions motivées, recours |

## Points d'accès principaux (`/api/v1/`)

| Méthode | Chemin | Usage |
|---|---|---|
| POST | `auth/login/` · `auth/beneficiary/login/` · `auth/refresh/` | Connexion staff, bénéficiaire (code dossier + PIN), renouvellement |
| GET | `auth/me/` | Profil, rôles et permissions par organisation |
| CRUD | `persons/`, `households/` | Dossiers (pas de suppression : archivage) |
| POST | `persons/{id}/transition/`, `merge/`, `unmerge/`, `beneficiary-access/` | Contrôle qualité, fusion, accès bénéficiaire |
| POST | `collect/submissions/` | Synchronisation d'un ménage complet depuis le terrain (rejouable) |
| GET/POST | `questionnaires/`, `survey-responses/`, `survey-responses/{id}/score/` | Enquêtes et scoring |
| CRUD | `programs/` + `transition/`, `rules/` | Programmes et règles versionnées |
| CRUD | `applications/` + `transition/`, `decide/` | Instruction et décision du comité |
| GET/POST | `decisions/`, `appeals/` + `resolve/` | Décisions et recours |
| GET | `reports/overview/`, `audit-events/` | Indicateurs agrégés, journal d'audit |
| GET/POST | `me/profile/`, `me/applications/`, `me/appeals/` | Espace bénéficiaire |

## Règles garanties par le code et les tests

- Isolation stricte entre organisations côté serveur ; le super-admin technique n'a aucun accès implicite aux données sociales.
- Aucun refus ni versement automatique : décision humaine motivée, quorum, conflit d'intérêts bloqué, recours examiné par une autre personne.
- Une réponse manquante n'est jamais un 0 favorable ; un score incomplet est marqué « peu fiable ».
- Rien ne s'efface : journal d'audit en ajout seul, versions figées, fusion réversible.
- Synchronisation terrain sans doublon (Idempotency-Key + `client_uuid`).

## Qualité

```bash
.venv/bin/ruff check .
.venv/bin/pytest            # 27 tests, scénarios de recette TC-01 à TC-17
```

La CI GitHub Actions (`.github/workflows/ci.yml`) exécute lint, contrôle des migrations et tests sur PostgreSQL.

## Comptes de démonstration

`seed_demo` crée une organisation fictive avec un compte par rôle (`admin.org`, `resp.programme`,
`travailleur.social`, `comite.1`, `comite.2`, `recenseur`, `superviseur`, `financeur`, `auditeur`) et `autre.org`
dans une autre organisation. Mot de passe : valeur de `DEMO_PASSWORD` dans `.env`. Aucune donnée réelle.
