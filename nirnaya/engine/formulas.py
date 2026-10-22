"""The stage formulas from CLAUDE.md, as pure Decimal functions. Sunk costs are excluded."""

from decimal import Decimal

from nirnaya.models.enums import Action

_ZERO = Decimal(0)


def chargeback_gain(*, p: Decimal, A: Decimal, h: Decimal, q: Decimal, F_pa: Decimal) -> Decimal:
    """Expected gain of challenging a chargeback over accepting it: p*A - h - q*F_pa."""
    return p * A - h - q * F_pa


def chargeback_margin(
    *, p: Decimal, A: Decimal, h: Decimal, q: Decimal, F_pa: Decimal, m: Decimal
) -> Decimal:
    """Chargeback stage: challenge iff p*A - h - q*F_pa > m."""
    return chargeback_gain(p=p, A=A, h=h, q=q, F_pa=F_pa) - m


def expected_chargeback_gain(
    *, p: Decimal, A: Decimal, h: Decimal, q: Decimal, F_pa: Decimal
) -> Decimal:
    """G_cb = max(0, p*A - h - q*F_pa): at chargeback the merchant would accept a losing case."""
    return max(_ZERO, chargeback_gain(p=p, A=A, h=h, q=q, F_pa=F_pa))


def pre_chargeback_margin(
    *, g_cb: Decimal, h_pre: Decimal, F_cb: Decimal, R: Decimal, F_pre: Decimal, m: Decimal
) -> Decimal:
    """Pre-chargeback stage: challenge iff G_cb - h_pre > F_cb + R - F_pre + m."""
    return g_cb - h_pre - (F_cb + R - F_pre + m)


def math_action(margin: Decimal) -> Action:
    """Strictly positive margin challenges; a tie accepts."""
    return Action.CHALLENGE if margin > 0 else Action.ACCEPT
