"""
Base Fundamentals Source — abstract interface for all data source adapters.

Adding a new asset class = implement this interface in a new file +
add the source name to instruments.yaml.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FundamentalsSnapshot:
    symbol: str
    asset_class: str
    timestamp: str
    signals: dict               # computed metrics for Python logic
    llm_summary: str            # human-readable summary for the LLM prompt
    source_ids: list = field(default_factory=list)  # which data sources were used
    error: str = ""             # non-empty if fetch failed


class BaseFundamentalsSource(ABC):
    """Abstract interface for fundamentals data sources."""

    @abstractmethod
    def fetch(self, symbol: str, as_of_date: str = None) -> FundamentalsSnapshot:
        """Fetch and compute fundamental signals for a symbol.

        Args:
            symbol: Ticker to analyze.
            as_of_date: Optional ISO 'YYYY-MM-DD'. When provided, sources must
                drop observations dated after as_of so backtests do not leak
                future fundamentals into a historical decision.

        Returns a FundamentalsSnapshot with computed signals and an LLM summary.
        """
        ...

    @abstractmethod
    def get_release_calendar(self) -> list[dict]:
        """Return upcoming data release events.
        Each entry: {event, day_of_week, time_et, description}."""
        ...
