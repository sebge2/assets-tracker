PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS assets (
    id       INTEGER PRIMARY KEY,
    symbol   TEXT NOT NULL UNIQUE,
    name     TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR'
);

CREATE TABLE IF NOT EXISTS transactions (
    id         INTEGER PRIMARY KEY,
    asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    type       TEXT NOT NULL CHECK (type IN ('BUY', 'SELL')),
    trade_date TEXT NOT NULL,            -- ISO 8601
    quantity   TEXT NOT NULL,            -- Decimal sérialisé (pas de float)
    unit_price TEXT NOT NULL,
    fees       TEXT NOT NULL DEFAULT '0'
);

CREATE TABLE IF NOT EXISTS prices (
    asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    price      TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_asset ON transactions(asset_id, trade_date);

-- Comptes cash : ajout purement additif (aucune table existante n'est modifiée).
CREATE TABLE IF NOT EXISTS cash_accounts (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    bank         TEXT NOT NULL,
    iban         TEXT NOT NULL UNIQUE,    -- normalisé, base chiffrée
    currency     TEXT NOT NULL DEFAULT 'EUR',
    opened_on    TEXT NOT NULL,           -- ISO 8601
    frequency    TEXT NOT NULL CHECK (frequency IN ('MONTHLY', 'ANNUAL')),
    payout_month INTEGER CHECK (payout_month BETWEEN 1 AND 12),  -- annuel uniquement
    payout_day   INTEGER CHECK (payout_day BETWEEN 1 AND 31)     -- annuel uniquement
);

-- Historique du taux de base (en %, Decimal sérialisé) : une ligne par changement.
CREATE TABLE IF NOT EXISTS cash_account_rates (
    id             INTEGER PRIMARY KEY,
    account_id     INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE CASCADE,
    effective_date TEXT NOT NULL,
    rate           TEXT NOT NULL
);

-- Historique des primes de fidélité : un taux nul retire la prime.
CREATE TABLE IF NOT EXISTS cash_account_bonuses (
    id             INTEGER PRIMARY KEY,
    account_id     INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE CASCADE,
    effective_date TEXT NOT NULL,
    rate           TEXT NOT NULL,
    after_months   INTEGER NOT NULL DEFAULT 0
);

-- Entrées et sorties : le montant est toujours positif, le sens dépend de `kind`.
CREATE TABLE IF NOT EXISTS cash_movements (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('OPENING', 'DEPOSIT', 'WITHDRAWAL', 'INTEREST', 'LOYALTY_BONUS')),
    movement_date TEXT NOT NULL,
    amount        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cash_rates_account ON cash_account_rates(account_id, effective_date);
CREATE INDEX IF NOT EXISTS idx_cash_bonuses_account
    ON cash_account_bonuses(account_id, effective_date);
CREATE INDEX IF NOT EXISTS idx_cash_movements_account ON cash_movements(account_id, movement_date);

-- Matières premières : ajout additif. Un actif est une matière première s'il a une ligne ici ;
-- ses achats et ventes sont des lignes ordinaires de `transactions` (BUY/SELL), son dernier
-- cours (dans l'unité de détention, en euros) une ligne de `prices`.
CREATE TABLE IF NOT EXISTS commodity_details (
    asset_id       INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    family         TEXT NOT NULL CHECK (family IN ('PRECIOUS', 'ENERGY', 'INDUSTRIAL', 'AGRICULTURAL')),
    form           TEXT NOT NULL CHECK (form IN ('PHYSICAL', 'ETC')),
    unit           TEXT NOT NULL,           -- g, kg, oz ou parts
    purity         TEXT,                    -- inutilisée (champ retiré de l'UI), conservée sans migration
    broker         TEXT,                    -- ETC : courtier ou compte-titres
    quote_symbol   TEXT,                    -- ticker du cours, NULL = pas de cours automatique
    quote_currency TEXT NOT NULL DEFAULT 'EUR',
    quote_unit     TEXT                     -- unité du cours (oz), NULL = unité de détention
);

-- Valorisations quotidiennes de tous les actifs (matières premières, fonds, actions…) :
-- cache (le fournisseur n'est interrogé qu'une fois par jour et par actif) et historique.
-- `value` est la valeur d'UNE unité de l'actif, en euros. Remplace `prices` (conservée, inutilisée).
CREATE TABLE IF NOT EXISTS valuations (
    asset_type     TEXT NOT NULL CHECK (asset_type IN
        ('STOCK', 'BOND', 'FUND', 'CRYPTO', 'CASH', 'TERM_NOTE', 'COMMODITY')),
    asset_id       INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    valuation_date TEXT NOT NULL,           -- ISO 8601 : le jour représenté
    value          TEXT NOT NULL,           -- Decimal sérialisé (pas de float)
    fetched_at     TEXT NOT NULL,           -- instant réel de l'appel au fournisseur
    PRIMARY KEY (asset_type, asset_id, valuation_date)
);

CREATE INDEX IF NOT EXISTS idx_valuations_asset ON valuations(asset_id, valuation_date);

-- Fonds hors ETF : souscriptions/rachats dans `transactions`, VL dans `valuations`.
CREATE TABLE IF NOT EXISTS fund_details (
    asset_id         INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    broker           TEXT,
    ter              TEXT,
    valuation_source TEXT NOT NULL CHECK (valuation_source IN ('YAHOO', 'MANUAL')),
    quote_symbol     TEXT
);

-- Obligations : achats/ventes dans `transactions` (prix unitaire en euros par titre, frais
-- nuls), cours dans `valuations` (valeur d'UN titre en euros). `valuation_source` est
-- l'origine du dernier cours enregistré.
CREATE TABLE IF NOT EXISTS bond_details (
    asset_id         INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    issuer           TEXT,
    nominal          TEXT NOT NULL,           -- Decimal sérialisé, en devise de l'obligation
    coupon_rate      TEXT NOT NULL,           -- en %
    frequency        TEXT NOT NULL CHECK (frequency IN ('ANNUAL', 'SEMIANNUAL', 'QUARTERLY')),
    maturity         TEXT NOT NULL,           -- ISO 8601
    valuation_source TEXT NOT NULL CHECK (valuation_source IN ('YAHOO', 'MANUAL'))
);

-- Coupon couru payé (achat) ou perçu (vente) d'une transaction : hors prix, hors PRU.
CREATE TABLE IF NOT EXISTS bond_accruals (
    transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    accrued        TEXT NOT NULL
);

-- Cache des taux de change vers l'euro : `rate` = euros pour UNE unité de `currency`
-- (Decimal sérialisé). Un taux par devise et par jour ; le dernier sert à convertir en EUR.
CREATE TABLE IF NOT EXISTS exchange_rates (
    currency   TEXT NOT NULL,
    rate_date  TEXT NOT NULL,               -- ISO 8601 : le jour du taux
    rate       TEXT NOT NULL,
    fetched_at TEXT NOT NULL,               -- instant réel de l'appel au fournisseur
    PRIMARY KEY (currency, rate_date)
);
