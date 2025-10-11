import json
import os
from pathlib import Path
from typing import Dict, Any

CONFIG_DIR = Path.home() / ".tricode"
CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULT_CONFIG = {
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4o-mini"
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
