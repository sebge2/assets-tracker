# Assets Tracker

Application desktop de gestion de portefeuille, 100 % locale : PySide6, SQLite chiffrée avec SQLCipher, cours via Yahoo Finance (yfinance). Le portefeuille n'est jamais envoyé à un service externe.

V1 : actions, obligations, fonds, cryptos, cash et bons de caisse, achats/ventes, positions, plus-values réalisées et latentes, multidevise avec valorisation en EUR, cours actualisés au démarrage et périodiquement (dernier prix conservé hors ligne), historique mensuel, performance annuelle, projections à intérêts composés et simulations avec versement mensuel.

Voir [AGENTS.md](AGENTS.md) pour l'architecture, les commandes et les conventions.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m assets_tracker
```

## Protection des données (macOS)

La base `portfolio.db` est chiffrée au repos avec SQLCipher (AES-256). Au premier
lancement, l'application génère une clé aléatoire de 256 bits et l'enregistre dans
le Trousseau macOS local, non synchronisé. À chaque ouverture, macOS exige Touch
ID avant que l'application ne lise cette clé et ouvre le portefeuille.

Touch ID n'est pas la clé de chiffrement et n'est jamais accessible à
l'application. La clé reste également protégée par la session macOS et le
Trousseau, mais le contrôle Touch ID est appliqué par l'application afin de rester
compatible avec une distribution PyInstaller non provisionnée. Prévoir un
export/sauvegarde chiffré avant de migrer vers un autre Mac ou d'effacer le
Trousseau.
Une base SQLite existante non chiffrée n'est jamais convertie automatiquement :
sa migration doit être une opération explicite et sauvegardée.
