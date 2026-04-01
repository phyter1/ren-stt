"""
Shared configuration loader for ren-stt.
Reads from ~/.config/ren-stt/config.json with sensible defaults.
"""

import json
import os

CONFIG_DIR = os.path.expanduser("~/.config/ren-stt")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "server": {
        "host": "0.0.0.0",
        "port": 8222,
    },
    "client": {
        "server_url": "http://localhost:8222",
        "hotkey": "option+space",
        "mode": "toggle",
        "sensitivity": 18,
        "indicator": True,
    },
}


def load():
    """Load config from disk, merged with defaults."""
    config = json.loads(json.dumps(DEFAULTS))  # deep copy

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            # Merge nested sections
            for section in ("server", "client"):
                if section in user:
                    config[section].update(user[section])
            # Preserve top-level keys (install_mode, etc.)
            for key, val in user.items():
                if key not in ("server", "client"):
                    config[key] = val
        except Exception as e:
            print(f"Warning: could not read {CONFIG_PATH}: {e}")

    return config


def save(config):
    """Save config to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Config saved to {CONFIG_PATH}")


def get_server_url(config):
    return config["client"]["server_url"]


def get_server_bind(config):
    return config["server"]["host"], config["server"]["port"]
