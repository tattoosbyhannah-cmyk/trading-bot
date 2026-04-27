"""
LLM Factory — creates LangChain ChatOpenAI instances from model registry config.

Usage:
    from config.llm_factory import create_llm
    llm = create_llm("technical_analyst", output_schema=TechnicalReport)
"""

from langchain_openai import ChatOpenAI
from config.model_registry import model_registry


def create_llm(agent_name: str, output_schema=None, max_tokens_override: int = None,
               temperature_override: float = None, extra_body: dict = None):
    """Create a ChatOpenAI instance routed to the correct endpoint for this agent.

    Args:
        agent_name: Agent identifier (must match agent_routing in models.yaml)
        output_schema: Pydantic model for structured output (optional)
        max_tokens_override: Override max_tokens from config
        temperature_override: Override temperature from config
        extra_body: Extra parameters to pass to the API
    """
    endpoint = model_registry.get_endpoint(agent_name)
    model_name = model_registry.get_model_name(agent_name)
    max_tokens = max_tokens_override or model_registry.get_max_tokens(agent_name)
    temperature = temperature_override if temperature_override is not None else model_registry.get_temperature(agent_name)
    seed = model_registry.get_seed(agent_name)

    kwargs = {
        "base_url": endpoint,
        "api_key": "not-needed",
        "model": model_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if seed is not None:
        kwargs["seed"] = seed
    if extra_body:
        kwargs["extra_body"] = extra_body

    llm = ChatOpenAI(**kwargs)

    if output_schema:
        return llm.with_structured_output(output_schema)
    return llm
