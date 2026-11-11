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

## Périmètre V1

- **Instruments** : création, modification et suivi, par type : `STOCK` (action), `BOND` (obligation), `FUND` (fonds), `CRYPTO`, `CASH`, `TERM_NOTE` (bon de caisse).
- **Transactions** : achats et ventes, avec quantité, prix et frais.
- **Valorisation** : positions, plus-values réalisées et latentes.
- **Obligations** : nominal, taux de coupon, fréquence de coupon et échéance.
- **Devises** : suivi multidevise, valorisation consolidée en EUR.
- **Cours** : actualisation au démarrage, rafraîchissement périodique, conservation du dernier prix connu.
- **Historique mensuel** : snapshot de la valorisation du portefeuille (et par position) en fin de mois, stocké en base, en EUR.
- **Performance annuelle** : calculée à partir de l'historique mensuel, en neutralisant les apports/retraits (rendement pondéré par le temps).
- **Projections** : capitalisation à intérêts composés annuels, à partir de la valeur actuelle et du taux de rendement annuel (par défaut la performance annuelle historique, modifiable).
- **Simulations** : projection avec versement mensuel régulier (montant, durée et taux paramétrables), comparable au scénario sans versement.
- **Persistance** : base SQLite locale, pleinement utilisable hors connexion.

Les projections sont des estimations indicatives, jamais des garanties : l'UI doit l'afficher.

Hors périmètre V1 : synchronisation cloud, multi-utilisateur. L'architecture doit pouvoir évoluer vers un suivi patrimonial plus large sans refonte.

État actuel : le schéma et les modèles ne couvrent pas encore le type d'instrument, les champs obligataires (nominal, coupon, fréquence, échéance), ni les taux de change, ni l'historique mensuel, la performance et les projections.

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
- **Devises** : chaque instrument a sa devise ; les valeurs sont calculées dans la devise native puis converties en EUR avec un taux de change (cours yfinance `XXXEUR=X`, dernier taux connu mis en cache comme les prix). Ne jamais additionner des montants de devises différentes.
- **Types d'instruments** : enum `InstrumentType` (`STOCK`, `BOND`, `FUND`, `CRYPTO`, `CASH`, `TERM_NOTE`) dans `domain/models.py`, colonne `type` avec contrainte `CHECK` en base. Le type pilote la valorisation et l'origine du cours :
  - `STOCK`, `FUND`, `CRYPTO` : cours yfinance (ticker, ex. `BTC-EUR`) ; saisie manuelle possible si le ticker n'est pas disponible (fonds notamment).
  - `BOND` : cours en % du nominal, paramètres obligataires requis.
  - `TERM_NOTE` (bon de caisse) : pas de cours de marché ; capital, taux, date de souscription et échéance saisis, valeur calculée par intérêts courus.
  - `CASH` : pas de cours, valeur = montant dans sa devise (converti en EUR).
  Les champs propres à un type ne sont obligatoires que pour ce type (validation dans `domain/`).
- **Obligations** : le cours est exprimé en % du nominal ; la valeur de marché = cours % × nominal × quantité. Les paramètres obligataires (nominal, coupon, fréquence, échéance) sont portés par l'instrument.
- **Hors ligne** : au démarrage et à chaque rafraîchissement, un échec réseau conserve le dernier prix connu (affiché avec sa date). Le rafraîchissement périodique s'exécute hors du thread UI.
- **Historique mensuel** : table dédiée (`monthly_snapshots`), un enregistrement par mois et par position plus un total, en EUR. Idempotent (le snapshot d'un mois se met à jour par upsert) et rattrapé au démarrage si des mois ont été manqués ; un mois passé est figé avec les derniers cours connus. Les snapshots ne sont jamais recalculés depuis les cours actuels.
- **Performance et projections** : logique pure dans `domain/` (`performance.py`, `projection.py`), en `Decimal`. Performance annuelle = rendement pondéré par le temps sur les snapshots mensuels, flux de transactions exclus. Projection : `V(n+1) = V(n) × (1 + r) + 12 × versement_mensuel` avec intérêts composés annuels ; le détail mois par mois doit rester testable avec des cas chiffrés.
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
