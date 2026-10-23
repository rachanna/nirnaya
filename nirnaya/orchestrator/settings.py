"""Runtime settings. Defaults are safe: DRY_RUN is on unless explicitly turned off.

Deadline buffers and budgets are ASSUMPTIONS (architecture sections 4.4 and 8.5).
"""

import os
from dataclasses import dataclass, field
from datetime import timedelta

from nirnaya.models.enums import Stage


def _dry_run_from_env() -> bool:
    # Only an explicit "false" or "0" turns writes on; anything else, including unset, is dry run.
    return os.environ.get("DRY_RUN", "true").strip().lower() not in {"false", "0"}


@dataclass(frozen=True)
class Settings:
    dry_run: bool = True
    # The write gateway refuses to act this close to Airwallex's deadline; the watchdog refers.
    deadline_buffer: dict[Stage, timedelta] = field(
        default_factory=lambda: {
            Stage.RFI: timedelta(days=2),
            Stage.PRE_CHARGEBACK: timedelta(hours=12),
            Stage.CHARGEBACK: timedelta(days=2),
            Stage.PRE_ARBITRATION: timedelta(days=2),
            Stage.ARBITRATION: timedelta(days=2),
        }
    )
    # Investigation stops earlier than the watchdog, so the watchdog is a backstop.
    investigation_buffer: dict[Stage, timedelta] = field(
        default_factory=lambda: {
            Stage.RFI: timedelta(days=3),
            Stage.PRE_CHARGEBACK: timedelta(hours=18),
            Stage.CHARGEBACK: timedelta(days=3),
            Stage.PRE_ARBITRATION: timedelta(days=3),
            Stage.ARBITRATION: timedelta(days=3),
        }
    )
    max_tool_calls: int = 8  # per cycle
    max_tokens_per_cycle: int = 60_000
    max_calls_per_round: int = 3
    max_turns_per_round: int = 6
    # A step that raises is audited and skipped so other cases keep moving; after this many
    # failures the case is referred (a write in flight is never interrupted).
    isolate_step_failures: bool = True
    max_step_failures: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(dry_run=_dry_run_from_env())
