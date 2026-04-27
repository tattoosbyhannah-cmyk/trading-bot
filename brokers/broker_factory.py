"""
Broker Factory — returns the appropriate broker adapter by name.

Usage:
    from brokers.broker_factory import get_broker
    broker = get_broker("alpaca")
    broker.submit_order(order)
"""

from brokers.base_broker import BaseBroker


def get_broker(broker_name: str) -> BaseBroker:
    if broker_name == "alpaca":
        from brokers.alpaca_broker import AlpacaBroker
        return AlpacaBroker()
    elif broker_name == "ibkr":
        raise NotImplementedError(
            "IBKR broker adapter not yet implemented. "
            "Create brokers/ibkr_broker.py implementing BaseBroker."
        )
    else:
        raise ValueError(f"Unknown broker: {broker_name}")
