"""Configuration loader. Secrets come from env, never from the yaml file."""
import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Config:
    model: str
    ollama_url: str
    ollama_api_key: str
    whitelist: list[str]
    system_prompt: str
    mcp_command: list[str]
    mcp_env: dict[str, str]
    max_tool_iterations: int = 3
    tool_timeout_s: float = 5.0


def load_config(path: str | None = None) -> Config:
    path = path or os.getenv("VOICE_AGENT_CONFIG", "config.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)

    # ${VAR:-default} expansion for ollama_url only (simple, no full templating)
    ollama_url = os.path.expandvars(raw["ollama_url"]) if "${" not in raw["ollama_url"] \
        else _expand(raw["ollama_url"])

    mcp_env = dict(raw.get("mcp_env", {}))
    mcp_env["HA_TOKEN"] = os.environ["HA_TOKEN"]

    return Config(
        model=raw["model"],
        ollama_url=os.getenv("OLLAMA_URL", ollama_url),
        ollama_api_key=os.environ["OLLAMA_API_KEY"],
        whitelist=list(raw["whitelist"]),
        system_prompt=raw["system_prompt"],
        mcp_command=list(raw["mcp_command"]),
        mcp_env=mcp_env,
        max_tool_iterations=int(raw.get("max_tool_iterations", 3)),
        tool_timeout_s=float(raw.get("tool_timeout_s", 5.0)),
    )


def _expand(value: str) -> str:
    """Expand ${VAR:-default} syntax."""
    import re

    def repl(m):
        var, default = m.group(1), m.group(2)
        return os.getenv(var, default)

    return re.sub(r"\$\{([A-Z_]+):-([^}]*)\}", repl, value)
