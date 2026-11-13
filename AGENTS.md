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
- **Matières premières** : métaux précieux, énergie, métaux industriels, agricole, détenus en physique (lingots, pièces) ou via des ETC/ETF ; achat, vente, modification, cours en euros (voir « Matières premières »).
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

État actuel : le schéma et les modèles ne couvrent pas encore le type d'instrument, les champs obligataires (nominal, coupon, fréquence, échéance), ni les taux de change, ni l'historique mensuel, la performance et les projections. Ils ne couvrent pas non plus les opérations autres que `BUY`/`SELL` sur les actifs (le schéma n'accepte que ces deux types, avec quantité et prix). Implémenté côté UI : le thème (`ui/theme.py`), la fenêtre principale avec menu latéral et la page **Comptes cash** (liste, fiche à onglets, dialogues) la page **Matières premières** (`ui/commodities_page.py`, moteur `domain/commodities.py`) et la page **Simulation** (`ui/projection_page.py`, moteur `domain/projection.py`). Pas encore d'assistant de transaction : les mouvements cash se saisissent depuis la fiche d'un compte (`cash_movements`). L'écran Portefeuille reste un tableau vide.

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
  - **Intérêts** : aucun crédit automatique. L'utilisateur saisit lui-même chaque versement comme mouvement de type `INTEREST` (intérêts) ou `LOYALTY_BONUS` (prime de fidélité), avec sa date et son montant. La fréquence (`ANNUAL` ou `MONTHLY`, et mois/jour du versement) ne sert qu'à l'information et aux estimations.
  - **Taux et primes** : les taux de base et la prime restent historisés (jamais réécrits) et servent aux estimations ; `domain.cash.accrued_interest` calcule des intérêts au prorata journalier, en `Decimal`, sans rien persister.
  - **Implémentation** : `domain/cash.py` (taux de base, prime, taux effectif, relevé, soldes, intérêts estimés, IBAN), `db/cash_repository.py` (`CashRepository`), `ui/cash_page.py`, `ui/cash_dialogs.py`. Tables `cash_accounts`, `cash_account_rates`, `cash_account_bonuses` (une définition de prime par ligne, un taux nul la retire) et `cash_movements` (`OPENING`, `DEPOSIT`, `WITHDRAWAL`, `INTEREST`, `LOYALTY_BONUS`, montant toujours positif). Taux effectif à une date = dernier taux de base ≤ date + prime de la dernière définition si `date ≥ ouverture + durée de détention`. Un mouvement qui rendrait le solde négatif à n'importe quelle date est refusé. La devise est figée après création, l'IBAN est unique.
  - **Total et évolution mensuelle** : la liste des comptes se termine par une ligne de total (solde, intérêts de l'année, taux moyen pondéré), une ligne par devise s'il y en a plusieurs. L'option « Évolution mensuelle » affiche, pour une devise à la fois, le solde de fin de mois de chaque compte et leur total (courbes + tableau avec variation du total ; périodes 12 mois, 36 mois, Tout). Les soldes sont recalculés depuis les mouvements (`monthly_balances` dans `domain/cash.py`), jamais stockés ; le mois en cours s'arrête à aujourd'hui ; avant l'existence d'un compte, il n'y a pas de solde (« — »), pas un solde de 0. Code : `ui/cash_evolution.py`, `ui/evolution_chart.py`.
- **Matières premières** : un actif est une matière première s'il a une ligne dans `commodity_details` (famille `PRECIOUS`/`ENERGY`/`INDUSTRIAL`/`AGRICULTURAL`, forme `PHYSICAL` ou `ETC`, unité g/kg/oz ou parts, courtier, symbole et unité du cours) ; ses achats et ventes sont des `transactions` ordinaires (`BUY`/`SELL`), ses cours sont des lignes de `valuations` (voir « Valorisations »). Position, PRU et plus-value sont recalculés par `domain/positions.py` (vente : PRU inchangé, plus-value réalisée) ; `domain/commodities.py` ajoute la conversion de cours, les aperçus d'achat/vente et la répartition par famille. `CommodityRepository` refuse tout achat, vente, correction ou suppression qui rendrait la quantité détenue négative à une date quelconque (`check_transaction`, essai à blanc), et garde toujours au moins une transaction par position.
  - **Cours** : en euros, par unité de détention. Un métal physique est coté en USD par once troy (`GC=F`, `SI=F`, `PL=F`, `PA=F`) puis converti (`convert_quote` : once → g/kg, taux `USDEUR=X`) ; un ETC est coté sous son propre symbole. Le réseau reste dans `market/` (`market/commodities.py`) ; l'actualisation tourne dans un `QThreadPool` (jamais d'accès SQLite hors du thread principal), au premier affichage puis toutes les 15 minutes, sans réinterroger une matière déjà valorisée aujourd'hui ; le bouton « Actualiser les cours » force un nouvel appel. Un échec garde le dernier cours, affiché avec sa date, et sera retenté au passage suivant ; une valorisation d'un jour passé, ou un échec, porte le badge « Cache ».
  - **Limites actuelles** : pas d'historique de cours de marché (la courbe de la fiche trace les prix de vos transactions, le PRU et le dernier cours), pas de variation du jour, devise de position fixée à l'euro, pas de contrats à terme. Le symbole est unique par position.
  - **Physique : pas de pureté, pas de frais séparés** : un métal physique se saisit au prix par unité « tout compris » (prime et livraison incluses), sans champ frais ni pureté, à l'achat comme à la vente. La colonne `purity` de `commodity_details` n'est plus utilisée mais reste en base (pas de suppression de colonne sans migration) ; les frais déjà saisis sur d'anciennes transactions restent comptés et modifiables depuis « Modifier ». Les ETC gardent leur champ « Frais ».
- **Valorisations** : table `valuations` (clé primaire `asset_type`, `asset_id`, `valuation_date`), commune à tous les types d'actifs (`InstrumentType` : `STOCK`, `BOND`, `FUND`, `CRYPTO`, `CASH`, `TERM_NOTE`, `COMMODITY`, avec contrainte `CHECK`). `value` est la valeur d'**une unité** de l'actif, en euros, stockée en `TEXT` (Decimal) ; `fetched_at` est l'instant réel de l'appel. Elle sert à la fois de **cache** (le fournisseur n'est interrogé qu'une fois par jour et par actif : `domain.valuation.assets_to_refresh`, `needs_refresh`) et d'**historique** (une ligne par jour, jamais modifiée une fois le jour passé ; réécrire le même jour met la ligne à jour). Accès par `db/valuation_repository.py` (`record`, `latest`, `on_day`, `history`, `valued_on`) ; tout nouveau type d'actif (fonds, actions, obligations, crypto) valorise par cette table, jamais par un cache propre. Un appel qui échoue n'écrit rien : le dernier cours connu reste affiché comme « Cache » et l'appel est retenté au passage suivant. Les appels réseau se font hors du thread principal ; l'écriture en base reste dans le thread principal. L'ancienne table `prices` n'est plus utilisée (conservée, intacte ; ses cours de matières premières ont été repris dans `valuations` par la migration 2). Valorisation d'une position = quantité × valorisation du jour.
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
- **Schéma DB** dans `db/schema.sql`. Toute évolution = migration versionnée (`PRAGMA user_version`), jamais de modification destructive silencieuse. `connect()` rejoue `schema.sql` (`CREATE ... IF NOT EXISTS`, ajouts purement additifs) puis applique `_migrate` : `user_version` 1 (type `LOYALTY_BONUS` des mouvements cash), 2 (reprise des cours de matières premières dans `valuations`). Toute modification d'une table existante passe par une nouvelle étape de `_migrate`.
- La base utilisateur vit dans le dossier AppData de Qt (`db/connection.py`), pas dans le repo (`*.db` est ignoré).
- PyInstaller : tout fichier de données lu à l'exécution (ex. `schema.sql`) doit être ajouté via `--add-data` dans `packaging/build.sh` et lu avec `importlib.resources`.

## Conventions

- Code et identifiants en anglais ; textes UI et documentation en français.
- Type hints partout (`mypy --strict`), `from __future__ import annotations`.
- Tout calcul financier a un test dans `tests/` avant d'être branché à l'UI.
- Tests UI avec `pytest-qt` ; mocker `market/` (pas de réseau en test).
- Commits courts, à l'impératif.
