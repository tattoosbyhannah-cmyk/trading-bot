"""
Data Source Factory — returns the appropriate fundamentals source by name.

Usage:
    from data_sources.source_factory import get_source
    source = get_source("eia_crude")
    snapshot = source.fetch("USO")
"""

from data_sources.base_source import BaseFundamentalsSource


def get_source(source_name: str) -> BaseFundamentalsSource:
    if source_name == "eia_crude":
        from data_sources.eia_crude import EIACrudeSource
        return EIACrudeSource()
    elif source_name == "eia_natgas":
        from data_sources.eia_natgas import EIANatgasSource
        return EIANatgasSource()
    elif source_name == "fred_gold":
        from data_sources.fred_gold import FREDGoldSource
        return FREDGoldSource()
    elif source_name == "fred_silver":
        from data_sources.fred_silver import FREDSilverSource
        return FREDSilverSource()
    else:
        raise ValueError(
            f"Unknown data source: {source_name}. "
            f"Create data_sources/{source_name}.py implementing BaseFundamentalsSource."
        )
