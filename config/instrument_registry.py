"""
Instrument Registry — single source of truth for all traded symbols.

Loads config/instruments.yaml and provides lookup functions.
Adding a new asset = adding a YAML block. No pipeline code changes needed.

Usage:
    from config.instrument_registry import registry

    registry.get_active_symbols()           # ["USO", "UNG", "GLD"]
    registry.get_instrument("USO")          # full config dict
    registry.get_symbols_by_class("oil")    # ["USO"]
    registry.get_sentiment_symbols("USO")   # ["USO", "XLE", "OIH", ...]
    registry.get_fundamentals_source("USO") # "eia_crude"
    registry.get_broker("USO")              # "alpaca"
    registry.get_asset_class("USO")         # "oil"
    registry.is_intraday_eligible("USO")    # True
    registry.get_min_atr("USO")             # 1.5
"""

from pathlib import Path
from typing import Optional

import yaml

_CONFIG_PATH = Path(__file__).parent / "instruments.yaml"


class InstrumentRegistry:
    def __init__(self, config_path: Path = _CONFIG_PATH):
        self._instruments = {}
        self._load(config_path)

    def _load(self, path: Path):
        with open(path) as f:
            data = yaml.safe_load(f)
        self._instruments = data.get("instruments", {})

    def reload(self):
        """Re-read the YAML file (e.g., after adding a new instrument)."""
        self._load(_CONFIG_PATH)

    def get_instrument(self, symbol: str) -> Optional[dict]:
        return self._instruments.get(symbol.upper())

    def get_active_symbols(self) -> list:
        """Return symbols where active: true (or active not set, for backwards compat)."""
        return sorted(
            sym for sym, cfg in self._instruments.items()
            if cfg.get("active", True)
        )

    def get_symbols_by_class(self, asset_class: str) -> list:
        return sorted(
            sym for sym, cfg in self._instruments.items()
            if cfg.get("asset_class") == asset_class
        )

    def get_asset_class(self, symbol: str) -> str:
        cfg = self.get_instrument(symbol)
        return cfg["asset_class"] if cfg else "unsupported"

    def get_sentiment_symbols(self, symbol: str) -> list:
        cfg = self.get_instrument(symbol)
        if not cfg:
            return [symbol.upper()]
        return cfg.get("sentiment_symbols", [symbol.upper()])

    def get_sentiment_group(self, symbol: str) -> str:
        cfg = self.get_instrument(symbol)
        return cfg.get("sentiment_group", "unknown") if cfg else "unknown"

    def get_fundamentals_source(self, symbol: str) -> str:
        cfg = self.get_instrument(symbol)
        if not cfg:
            return "unsupported"
        return cfg.get("data_sources", {}).get("fundamentals", "unsupported")

    def get_macro_sources(self, symbol: str) -> list:
        cfg = self.get_instrument(symbol)
        if not cfg:
            return []
        return cfg.get("data_sources", {}).get("macro", [])

    def get_broker(self, symbol: str) -> str:
        cfg = self.get_instrument(symbol)
        return cfg.get("broker", "alpaca") if cfg else "alpaca"

    def is_intraday_eligible(self, symbol: str) -> bool:
        cfg = self.get_instrument(symbol)
        return cfg.get("intraday_eligible", False) if cfg else False

    def get_min_atr(self, symbol: str) -> float:
        cfg = self.get_instrument(symbol)
        return cfg.get("min_daily_atr_pct", 1.5) if cfg else 1.5

    def has_roll_mechanics(self, symbol: str) -> bool:
        cfg = self.get_instrument(symbol)
        return cfg.get("roll_mechanics", False) if cfg else False


# Singleton — imported by all consumers
registry = InstrumentRegistry()
