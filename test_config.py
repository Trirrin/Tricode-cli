#!/usr/bin/env python

import json
import tempfile
import os
from pathlib import Path

def test_config():
    from agent.config import load_config, ensure_config_exists, CONFIG_FILE, CONFIG_DIR
    
    original_home = os.environ.get('HOME')
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['HOME'] = tmpdir
        
        test_config_dir = Path(tmpdir) / ".tricode"
        test_config_file = test_config_dir / "settings.json"
        
        from importlib import reload
        import agent.config as config_module
        config_module.CONFIG_DIR = test_config_dir
        config_module.CONFIG_FILE = test_config_file
        
        ensure_config_exists()
        
        assert test_config_dir.exists(), "Config directory not created"
        assert test_config_file.exists(), "Config file not created"
        print(f"✓ Config directory and file created")
        
        config = load_config()
        assert "openai_api_key" in config, "Missing openai_api_key"
        assert "openai_base_url" in config, "Missing openai_base_url"
        assert "openai_model" in config, "Missing openai_model"
        print(f"✓ Default config loaded correctly")
        
        test_data = {
            "openai_api_key": "test-key",
            "openai_base_url": "https://custom.api.com/v1",
            "openai_model": "gpt-4"
        }
        with open(test_config_file, 'w') as f:
            json.dump(test_data, f)
        
        config = load_config()
        assert config["openai_api_key"] == "test-key", "API key mismatch"
        assert config["openai_base_url"] == "https://custom.api.com/v1", "Base URL mismatch"
        assert config["openai_model"] == "gpt-4", "Model mismatch"
        print(f"✓ Custom config loaded correctly")
    
    if original_home:
        os.environ['HOME'] = original_home
    else:
        os.environ.pop('HOME', None)

if __name__ == "__main__":
    print("Running config tests...\n")
    test_config()
    print("\n✅ All config tests passed!")
