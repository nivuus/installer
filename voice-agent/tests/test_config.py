import os
from voice_agent.config import load_config


def test_load_config_reads_yaml_and_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "model: qwen3:4b\n"
        "ollama_url: http://gw:11435\n"
        "whitelist: [call_service]\n"
        "max_tool_iterations: 3\n"
        "tool_timeout_s: 5.0\n"
        "mcp_command: [python, server.py]\n"
        "mcp_env: {HA_URL: https://ha}\n"
        "system_prompt: bonjour\n"
    )
    monkeypatch.setenv("OLLAMA_API_KEY", "secret-key")
    monkeypatch.setenv("HA_TOKEN", "ha-token")

    cfg = load_config(str(cfg_file))

    assert cfg.model == "qwen3:4b"
    assert cfg.ollama_url == "http://gw:11435"
    assert cfg.whitelist == ["call_service"]
    assert cfg.ollama_api_key == "secret-key"
    assert cfg.mcp_env["HA_TOKEN"] == "ha-token"
    assert cfg.mcp_env["HA_URL"] == "https://ha"
    assert cfg.max_tool_iterations == 3
