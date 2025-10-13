import json
import os
from pathlib import Path
from typing import Dict, Any

CONFIG_DIR = Path.home() / ".tricode"
CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULT_CONFIG = {
    "default_provider": "openai",
    "providers": {
        "openai": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "provider": "openai",
            "model": "gpt-4o-mini"
        },
        "anthropic": {
            "api_key": "",
            "base_url": "https://api.anthropic.com/v1",
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022"
        }
    }
}

def ensure_config_exists() -> None:
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True)
    
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)

def load_config() -> Dict[str, Any]:
    ensure_config_exists()
    
    config = DEFAULT_CONFIG.copy()
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            file_config = json.load(f)
            config.update(file_config)
    except Exception:
        pass
    
    env_prefix = "TRICODE_"
    for key in config.keys():
        env_key = env_prefix + key.upper()
        env_value = os.getenv(env_key)
        if env_value is not None:
            config[key] = env_value
    
    return config

def get_config_value(key: str, default: Any = None) -> Any:
    config = load_config()
    return config.get(key, default)

def get_provider_config(provider_name: str = None) -> Dict[str, Any]:
    config = load_config()
    
    if provider_name is None:
        provider_name = config.get("default_provider", "openai")
    
    providers = config.get("providers", {})
    if provider_name not in providers:
        raise ValueError(f"Provider '{provider_name}' not found in config. Available: {list(providers.keys())}")
    
    provider_config = providers[provider_name].copy()
    
    env_prefix = f"TRICODE_{provider_name.upper()}_"
    for key in ["api_key", "base_url", "provider", "model"]:
        env_key = env_prefix + key.upper()
        env_value = os.getenv(env_key)
        if env_value is not None:
            provider_config[key] = env_value
    
    if not provider_config.get("api_key"):
        raise ValueError(f"API key not configured for provider '{provider_name}'")
    
    return provider_config
