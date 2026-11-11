# Assets Tracker

Application desktop de gestion de portefeuille, 100 % locale : PySide6, SQLite, cours via Yahoo Finance (yfinance). Le portefeuille n'est jamais envoyé à un service externe.

V1 : actions, obligations, fonds, cryptos, cash et bons de caisse, achats/ventes, positions, plus-values réalisées et latentes, multidevise avec valorisation en EUR, cours actualisés au démarrage et périodiquement (dernier prix conservé hors ligne), historique mensuel, performance annuelle, projections à intérêts composés et simulations avec versement mensuel.

Voir [AGENTS.md](AGENTS.md) pour l'architecture, les commandes et les conventions.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m assets_tracker
```
