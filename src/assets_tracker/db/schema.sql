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
