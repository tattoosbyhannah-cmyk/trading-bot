"""
Model Registry — routes agents to LLM endpoints via config/models.yaml.

Usage:
    from config.model_registry import model_registry

    endpoint = model_registry.get_endpoint("technical_analyst")
    config = model_registry.get_model_for_agent("technical_analyst")
    all_eps = model_registry.get_all_endpoints()
"""

from pathlib import Path
from typing import Optional

import yaml

_CONFIG_PATH = Path(__file__).parent / "models.yaml"


class ModelRegistry:
    def __init__(self, config_path: Path = _CONFIG_PATH):
        self._models = {}
        self._routing = {}
        self._load(config_path)

    def _load(self, path: Path):
        with open(path) as f:
            data = yaml.safe_load(f)
        self._models = data.get("models", {})
        self._routing = data.get("agent_routing", {})

    def reload(self):
        self._load(_CONFIG_PATH)

    def get_model_for_agent(self, agent_name: str) -> dict:
        """Returns full model config for this agent."""
        model_key = self._routing.get(agent_name, "default")
        return self._models.get(model_key, self._models.get("default", {}))

    def get_endpoint(self, agent_name: str) -> str:
        """Returns the inference endpoint URL for this agent."""
        config = self.get_model_for_agent(agent_name)
        return config.get("endpoint", "http://127.0.0.1:8081/v1")

    def get_model_name(self, agent_name: str) -> str:
        """Returns the model name string to pass in API calls."""
        config = self.get_model_for_agent(agent_name)
        return config.get("model_name", "qwen3-thinking")

    def get_max_tokens(self, agent_name: str) -> int:
        config = self.get_model_for_agent(agent_name)
        return config.get("max_tokens", 4096)

    def get_temperature(self, agent_name: str) -> float:
        config = self.get_model_for_agent(agent_name)
        return config.get("temperature", 0.0)

    def get_seed(self, agent_name: str) -> Optional[int]:
        config = self.get_model_for_agent(agent_name)
        return config.get("seed")

    def get_all_endpoints(self) -> set:
        """Returns all unique endpoints for health checking."""
        endpoints = set()
        for model_key in set(self._routing.values()):
            model = self._models.get(model_key, {})
            ep = model.get("endpoint")
            if ep:
                endpoints.add(ep)
        # Always include default
        default = self._models.get("default", {}).get("endpoint")
        if default:
            endpoints.add(default)
        return endpoints

    def get_all_model_names(self) -> dict:
        """Returns {endpoint: model_display_name} for health check display."""
        names = {}
        for model in self._models.values():
            ep = model.get("endpoint")
            if ep:
                names[ep] = model.get("name", "unknown")
        return names


# Singleton
model_registry = ModelRegistry()
