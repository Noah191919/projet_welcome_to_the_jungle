# Flight Payment Sync Service — README

## Description
Service FastAPI pour importer des fichiers CSV (clients + achats) dans une base SQLite et synchroniser les données vers une API externe.  
La résilience réseau est gérée via un worker cron (re-traitement asynchrone et persistance d'échecs dans un Outbox) plutôt qu'un retry synchrone dans la requête HTTP.

## Installation (local)
1. Créez et activez un environnement virtuel Python >= 3.13
   - python -m venv .venv
   - source .venv/bin/activate

2. Installez les dépendances
   - pip install -r <(python -c "import tomllib,sys;print('\\n'.join([l for l in open('pyproject.toml') if l.strip().startswith('fastapi') or l.strip().startswith('httpx') or l.strip().startswith('sqlalchemy') or l.strip().startswith('pytest')]))") 
   ou
   - pip install -e payment-app[deps]  # selon setup local

3. (Optionnel) Pour les tests, on utilise une base SQLite en mémoire :
   - export DATABASE_URL="sqlite:///:memory:"

## Démarrage de l'API
Depuis la racine du dépôt :
- cd payment-app
- uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

L'API sera disponible sur http://127.0.0.1:8000

## Endpoints exemples (curl)

1) Import CSV (POST /api/import-csv)
- Exécuter (remplacez les chemins par vos fichiers locaux) :
curl --location 'http://127.0.0.1:8000/api/import-csv' \
  --header 'Content-Type: application/json' \
  --data '{
    "customers_file_path": "/opt/custexport/customers.csv",
    "purchased_file_path": "/opt/custexport/purchases.csv"
  }'

- Réponses attendues :
  - 201 Created (import OK)
  - 400 Bad Request (format CSV invalide)
  - 404 Not Found (fichier introuvable)
  - 500 Internal Server Error (erreur inattendue)

2) Déclencher la synchronisation (POST /api/send-customers)
curl --location 'http://127.0.0.1:8000/api/send-customers' --request POST

- Réponses attendues :
  - 200 OK (cron trigger exécuté — le worker s'occupe des envois)
  - 500 Internal Server Error (erreur serveur)

## Tests
Depuis la racine du dépôt :
- pytest -q

Les tests utilisent par défaut `sqlite:///:memory:` pour isoler la base.

## Design — stratégie de résilience réseau
Plutôt que d'implémenter des retries synchrones imbriqués (qui peuvent complexifier le flux et bloquer des requêtes utilisateur), j'ai opté pour :
- un Outbox local (fichiers JSON) pour persister les payloads échoués,
- un worker cron (CronSyncWorker) qui reprend périodiquement les envois et applique une logique d'incrémentation d'`attempt_number` pour logger les échecs permanents (au bout de 5 tentatives).

Avantages :
- Robustesse : les échecs sont durables (persistés) et réessayés séparément ;
- Observabilité : les tentatives et échecs sont comptés dans la BDD / logs ;
- Simplicité côté requêtes utilisateur (les endpoints restent rapides).

## Fichiers importants
- [payment-app/app/main.py](payment-app/app/main.py) — endpoints API (`/api/import-csv`, `/api/send-customers`)
- [payment-app/app/services.py](payment-app/app/services.py) — import CSV, construction payloads, client externe
- [payment-app/app/outbox.py](payment-app/app/outbox.py) — persistance des payloads échoués
- [payment-app/app/cron.py](payment-app/app/cron.py) — worker cron qui gère les retries asynchrones
- [payment-app/data/customers.csv](payment-app/data/customers.csv) — exemple customers.csv
- [payment-app/data/purchases.csv](payment-app/data/purchases.csv) — exemple purchases.csv
- [payment-app/tests](payment-app/tests) — suite de tests

## Remarques
- Adaptez `DATABASE_URL` dans `payment-app/app/config.py` si vous souhaitez utiliser un fichier SQLite persistant.
- L'URL de l'API externe de test est définie dans `app.config.settings.MOCK_API_URL`.
