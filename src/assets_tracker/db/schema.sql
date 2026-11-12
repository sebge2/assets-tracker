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
