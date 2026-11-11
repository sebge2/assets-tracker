from __future__ import annotations

import os

# Les tests d'interface tournent sans écran.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
