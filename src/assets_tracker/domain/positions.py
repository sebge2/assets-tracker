"""Moteur financier : calcul des positions et du PRU (coût moyen pondéré)."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from assets_tracker.domain.models import Position, Transaction, TransactionType

ZERO = Decimal(0)


def compute_position(asset_id: int, transactions: Iterable[Transaction]) -> Position:
    """Rejoue les transactions d'un actif par ordre chronologique.

    Achat : le PRU est recalculé, frais inclus.
    Vente : le PRU est inchangé, la plus-value réalisée est cumulée (frais déduits).
    """
    quantity = ZERO
    cost = ZERO  # coût total de la position ouverte
    realized = ZERO

    for tx in sorted(transactions, key=lambda t: (t.trade_date, t.id or 0)):
        if tx.asset_id != asset_id:
            raise ValueError(f"Transaction {tx.id} n'appartient pas à l'actif {asset_id}")
        if tx.type is TransactionType.BUY:
            quantity += tx.quantity
            cost += tx.quantity * tx.unit_price + tx.fees
        else:
            if tx.quantity > quantity:
                raise ValueError("Vente supérieure à la quantité détenue")
            pru = cost / quantity
            realized += tx.quantity * (tx.unit_price - pru) - tx.fees
            quantity -= tx.quantity
            cost -= tx.quantity * pru

    pru = cost / quantity if quantity else ZERO
    return Position(asset_id, quantity, pru, realized)
