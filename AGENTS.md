# AGENTS.md

Assets Tracker — application desktop locale de suivi de portefeuille. Les données restent sur la machine ; Internet n'est utilisé que pour récupérer des cours.

## Stack

| Couche | Techno | Emplacement |
|---|---|---|
| UI | PySide6 (Qt) | `src/assets_tracker/ui/` |
| Moteur financier | Python pur | `src/assets_tracker/domain/` |
| Stockage | SQLite (`sqlite3` stdlib) | `src/assets_tracker/db/` |
| Cours | yfinance | `src/assets_tracker/market/` |
| Packaging | PyInstaller | `packaging/build.sh` |

Python ≥ 3.11. Gestion des dépendances dans `pyproject.toml` (extra `dev`).

## Commandes

```bash
python3.14 -m venv .venv && source .venv/bin/activate   # ou tout Python ≥ 3.11
pip install -e ".[dev]"
python -m assets_tracker          # lancer l'app
pytest                           # tests
ruff check . && ruff format .    # lint + format
mypy src                         # typage strict
./packaging/build.sh             # exécutable dans dist/
```

## Architecture et règles

- **Dépendances unidirectionnelles** : `ui` → `domain`, `db`, `market`. `domain` ne dépend de rien (ni Qt, ni SQLite, ni réseau) : il reste testable seul.
- **Argent = `Decimal`**, jamais `float`. En SQLite, les montants et quantités sont stockés en `TEXT`. Les cours yfinance sont convertis via `Decimal(str(x))`.
- **PRU** = coût moyen pondéré, frais d'achat inclus ; une vente laisse le PRU inchangé et réalise une plus-value (voir `domain/positions.py`). Les positions sont **recalculées** depuis les transactions, jamais stockées.
- **Réseau isolé dans `market/`** : aucun autre module n'accède à Internet. Les échecs réseau ne doivent jamais bloquer l'app (retour `None`, dernier cours en cache table `prices`). Ne jamais envoyer le portefeuille à un service tiers.
- **UI non bloquante** : appels réseau hors du thread Qt (`QThread`/`QThreadPool`).
- **Schéma DB** dans `db/schema.sql`. Toute évolution = migration versionnée (`PRAGMA user_version`), jamais de modification destructive silencieuse.
- La base utilisateur vit dans le dossier AppData de Qt (`db/connection.py`), pas dans le repo (`*.db` est ignoré).
- PyInstaller : tout fichier de données lu à l'exécution (ex. `schema.sql`) doit être ajouté via `--add-data` dans `packaging/build.sh` et lu avec `importlib.resources`.

## Conventions

- Code et identifiants en anglais ; textes UI et documentation en français.
- Type hints partout (`mypy --strict`), `from __future__ import annotations`.
- Tout calcul financier a un test dans `tests/` avant d'être branché à l'UI.
- Tests UI avec `pytest-qt` ; mocker `market/` (pas de réseau en test).
- Commits courts, à l'impératif.
