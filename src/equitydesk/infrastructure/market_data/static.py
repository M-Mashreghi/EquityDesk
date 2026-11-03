"""Market-data adapter over explicit, timestamped observations (manual entry or a JSON file).

No paid provider is required in v1: prices come from an analyst-maintained file with their
source and observation time, and are filtered point-in-time by the workflow.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from equitydesk.domain.market import MarketObservation


class StaticMarketData:
    def __init__(self, observations: Iterable[MarketObservation] = ()) -> None:
        self._observations = list(observations)

    @classmethod
    def from_json(cls, path: Path) -> StaticMarketData:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(MarketObservation.model_validate(o) for o in raw["observations"])

    def observations(self, security_ids: Sequence[str], as_of: datetime) -> list[MarketObservation]:
        wanted = set(security_ids)
        # Later observations are returned too; `observation_as_of` refuses to use them, and
        # raises FutureLeakageError when nothing earlier exists.
        return [o for o in self._observations if o.security_id in wanted]
