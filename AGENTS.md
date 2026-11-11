# AGENTS.md

Assets Tracker — application desktop locale de suivi de portefeuille. Les données restent sur la machine ; Internet n'est utilisé que pour récupérer des cours.

## Stack

| Couche | Techno | Emplacement |
|---|---|---|
| UI | PySide6 (Qt) | `src/assets_tracker/ui/` |
| Moteur financier | Python pur | `src/assets_tracker/domain/` |
| Stockage | SQLCipher + Trousseau macOS / Touch ID | `src/assets_tracker/db/` |
| Cours | yfinance | `src/assets_tracker/market/` |
| Packaging | PyInstaller | `packaging/build.sh` |

Python ≥ 3.11. Gestion des dépendances dans `pyproject.toml` (extra `dev`).

## Périmètre V1

- **Instruments** : création, modification et suivi, par type : `STOCK` (action), `BOND` (obligation), `FUND` (fonds), `CRYPTO`, `CASH`, `TERM_NOTE` (bon de caisse).
- **Transactions** : les opérations et les champs saisis dépendent du type d'instrument (voir « Opérations par instrument »). Saisie via un assistant (wizard) en 4 étapes : instrument, opération, détails, récapitulatif.
- **Comptes cash** : gestion de comptes (nom, banque, IBAN, devise) avec taux d'intérêt, historique des taux, prime de fidélité et fréquence de versement (voir « Comptes cash »). Tout dépôt/retrait est rattaché à un compte.
- **Valorisation** : positions, plus-values réalisées et latentes.
- **Obligations** : nominal, taux de coupon, fréquence de coupon et échéance.
- **Devises** : suivi multidevise, valorisation consolidée en EUR.
- **Cours** : actualisation au démarrage, rafraîchissement périodique, conservation du dernier prix connu.
- **Historique mensuel** : snapshot de la valorisation du portefeuille (et par position) en fin de mois, stocké en base, en EUR.
- **Performance annuelle** : calculée à partir de l'historique mensuel, en neutralisant les apports/retraits (rendement pondéré par le temps).
- **Projections** : capitalisation à intérêts composés annuels, à partir de la valeur actuelle et du taux de rendement annuel (par défaut la performance annuelle historique, modifiable).
- **Simulations** : projection avec versement mensuel régulier (montant, durée et taux paramétrables), comparable au scénario sans versement.
- **Interface** : thème sombre, écrans Portefeuille, Transactions, Comptes cash, Performance et Simulation (voir « Design system »).
- **Persistance** : base SQLCipher locale chiffrée, pleinement utilisable hors connexion.

Les projections sont des estimations indicatives, jamais des garanties : l'UI doit l'afficher.

Hors périmètre V1 : synchronisation cloud, multi-utilisateur. L'architecture doit pouvoir évoluer vers un suivi patrimonial plus large sans refonte.

État actuel : le schéma et les modèles ne couvrent pas encore le type d'instrument, les champs obligataires (nominal, coupon, fréquence, échéance), ni les taux de change, ni l'historique mensuel, la performance et les projections. Ils ne couvrent pas non plus les opérations autres que `BUY`/`SELL` sur les actifs (le schéma n'accepte que ces deux types, avec quantité et prix). Implémenté côté UI : le thème (`ui/theme.py`), la fenêtre principale avec menu latéral et la page **Comptes cash** (liste, fiche à onglets, dialogues) et la page **Simulation** (`ui/projection_page.py`, moteur `domain/projection.py`). Pas encore d'assistant de transaction : les mouvements cash se saisissent depuis la fiche d'un compte (`cash_movements`). L'écran Portefeuille reste un tableau vide.

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
- **Opérations par instrument** : le type d'opération et les champs saisis dépendent de l'instrument ; la validation vit dans `domain/`. Le libellé UI peut différer du code. Le schéma actuel (`BUY`/`SELL`) devra être étendu par migration.

  | Instrument | Entrée | Sortie | Champs spécifiques |
  |---|---|---|---|
  | `STOCK` | Achat | Vente | symbole, devise, quantité, prix, frais |
  | `BOND` | Achat | Vente, Remboursement | titres, nominal, prix en % du nominal, coupon, échéance, coupon couru |
  | `FUND` | Souscription | Rachat | parts, valeur liquidative, frais d'entrée ou de sortie en % |
  | `CRYPTO` | Achat | Vente | jeton, plateforme, quantité (8 décimales), frais en € |
  | `CASH` | Dépôt | Retrait | compte, montant (ni quantité ni prix) |
  | `TERM_NOTE` | Souscription | Remboursement | émetteur, montant, taux, échéance, mode de versement des intérêts |

  À la sortie, on choisit un actif déjà détenu : la quantité est plafonnée à la position, le PRU reste inchangé et la plus-value réalisée est calculée. Un remboursement obligataire se fait au nominal, sans prix : le capital et le dernier coupon suffisent. Le coupon couru payé à l'achat est un coût ; perçu à la vente, c'est un revenu hors plus-value.
- **Comptes cash** : entité `cash_accounts` (nom, banque, IBAN, devise). L'IBAN reste en local, affiché masqué dans l'UI (`FR76 •••• •••• 0143`). Une transaction `CASH` référence toujours un compte, dont elle reprend la devise ; un retrait ne peut pas dépasser le solde.
  - **Taux et historique** : un compte a un taux de base et une prime de fidélité, tous deux datés (date d'effet). Un changement de taux ajoute une ligne, jamais de modification de l'ancienne : l'historique des taux d'un compte est conservé. Taux effectif = taux de base + prime une fois la condition remplie.
  - **Prime de fidélité** : taux additionnel conditionné par une durée de détention en mois. Quand la condition est atteinte, la prime s'ajoute à l'historique à cette date.
  - **Versement des intérêts** : fréquence `ANNUAL` ou `MONTHLY`. Mensuel : versé le 1er de chaque mois, sans autre saisie. Annuel : mois et jour du versement (ex. 31 décembre).
  - **Intérêts versés** : conservés par compte avec leur date. Le calcul (taux × solde au prorata) vit dans `domain/` (pur, `Decimal`) et a des cas chiffrés ; les intérêts estimés dans l'UI sont des estimations, à l'image des projections.
  - **Implémentation** : `domain/cash.py` (taux de base, prime, taux effectif, relevé, soldes, intérêts estimés, IBAN), `db/cash_repository.py` (`CashRepository`), `ui/cash_page.py`, `ui/cash_dialogs.py`. Tables `cash_accounts`, `cash_account_rates`, `cash_account_bonuses` (une définition de prime par ligne, un taux nul la retire) et `cash_movements` (`OPENING`, `DEPOSIT`, `WITHDRAWAL`, `INTEREST`, montant toujours positif). Taux effectif à une date = dernier taux de base ≤ date + prime de la dernière définition si `date ≥ ouverture + durée de détention`. Un mouvement qui rendrait le solde négatif à n'importe quelle date est refusé. La devise est figée après création, l'IBAN est unique.
  - **Décision** : les intérêts versés sont des mouvements `INTEREST` saisis par l'utilisateur ; ils ne sont pas générés automatiquement à la date de versement (à faire plus tard, sans changer le schéma). Les totaux de l'écran sont calculés par devise (jamais de somme entre devises) et le taux moyen est masqué en multidevise.
- **Hors ligne** : au démarrage et à chaque rafraîchissement, un échec réseau conserve le dernier prix connu (affiché avec sa date). Le rafraîchissement périodique s'exécute hors du thread UI.
- **Historique mensuel** : table dédiée (`monthly_snapshots`), un enregistrement par mois et par position plus un total, en EUR. Idempotent (le snapshot d'un mois se met à jour par upsert) et rattrapé au démarrage si des mois ont été manqués ; un mois passé est figé avec les derniers cours connus. Les snapshots ne sont jamais recalculés depuis les cours actuels.
- **Performance et projections** : logique pure dans `domain/` (`performance.py`, `projection.py`), en `Decimal`. Performance annuelle = rendement pondéré par le temps sur les snapshots mensuels, flux de transactions exclus. Projection : `V(n+1) = V(n) × (1 + r) + 12 × versement_mensuel` avec intérêts composés annuels ; le détail année par année doit rester testable avec des cas chiffrés (référence : 25 000 €, 500 €/mois, 6 %, 20 ans → 300 891,93 €). L'écran Simulation affiche aussi le scénario sans versement ; ses valeurs de départ (25 000 €, 500 €, 6 %, 20 ans) sont fixes en attendant la valorisation du portefeuille et la performance historique, qui doivent les remplacer.
- **UI non bloquante** : appels réseau hors du thread Qt (`QThread`/`QThreadPool`).
- **Design system** : thème sombre dans `ui/theme.py` (tokens + feuille de style Qt), appliqué au démarrage par `apply_theme(app)`. Aucune couleur en dur dans les widgets : importer les tokens. Les mocks de référence sont dans le canvas « Assets Tracker Design System » (fondations, Portefeuille, Transactions et wizard, Comptes cash, Performance, Simulation).
  - Fond `#0B0D12`, surfaces `#12151C` / `#181C26` / `#202533`, accent `#7C8CFF`, gain `#3DDC97`, perte `#FF7A8A`, alerte `#F5B84B`.
  - Polices : Space Grotesk (titres), Inter (texte), JetBrains Mono tabulaire pour tous les chiffres. Ces polices ne sont pas embarquées : Qt retombe sur une police système.
  - Gain et perte portent toujours un signe et une flèche (▲ / ▼), jamais la couleur seule. `variation_color()` donne la couleur sémantique.
  - Un prix en cache (cours non actualisé) s'affiche avec un badge « Cache » couleur alerte.
  - États focus avec anneau accent de 2 px. Les mocks utilisent des champs et boutons de 44 px ; `theme.py` est à 36 px pour l'instant (à aligner).
- **Base chiffrée** : la base est ouverte uniquement via `db/connection.py` avec SQLCipher ; ne jamais employer `sqlite3` pour une base de portefeuille. Une clé aléatoire de 256 bits est créée au premier lancement et stockée dans le Trousseau macOS local, avec `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`. `db/keychain.py` demande Touch ID avec `LocalAuthentication` avant chaque lecture de la clé. Cette séparation évite `errSecMissingEntitlement` (`-34018`) dans les builds PyInstaller locaux ; Touch ID ne fournit jamais la clé elle-même.
- **Clé et récupération** : aucune clé, copie du Trousseau ou base en clair ne doit être journalisée, exportée ou versionnée. Les exports/sauvegardes chiffrés et la migration vers un autre Mac doivent être des flux explicites ; une base SQLite non chiffrée est refusée, jamais chiffrée/migrée silencieusement.
- **Schéma DB** dans `db/schema.sql`. Toute évolution = migration versionnée (`PRAGMA user_version`), jamais de modification destructive silencieuse. Dette : `connect()` rejoue encore tout `schema.sql` (`CREATE ... IF NOT EXISTS`) sans `user_version` ; les tables `cash_*` ont été ajoutées de cette façon, sans toucher aux tables existantes. Le premier changement qui modifie une table existante doit introduire le mécanisme de migration.
- La base utilisateur vit dans le dossier AppData de Qt (`db/connection.py`), pas dans le repo (`*.db` est ignoré).
- PyInstaller : tout fichier de données lu à l'exécution (ex. `schema.sql`) doit être ajouté via `--add-data` dans `packaging/build.sh` et lu avec `importlib.resources`.

## Conventions

- Code et identifiants en anglais ; textes UI et documentation en français.
- Type hints partout (`mypy --strict`), `from __future__ import annotations`.
- Tout calcul financier a un test dans `tests/` avant d'être branché à l'UI.
- Tests UI avec `pytest-qt` ; mocker `market/` (pas de réseau en test).
- Commits courts, à l'impératif.
