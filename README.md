# Assets Tracker

Application desktop de gestion de portefeuille, 100 % locale : PySide6, SQLite, cours via Yahoo Finance (yfinance). Le portefeuille n'est jamais envoyé à un service externe.

Voir [AGENTS.md](AGENTS.md) pour l'architecture, les commandes et les conventions.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m assets_tracker
```
