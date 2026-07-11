import os
import re

from .settings import CONFIG_FILE


def load_config(config_file: str = CONFIG_FILE) -> dict:
    """config.yaml を読み込む。yaml ライブラリがない場合は簡易パーサーで対応。"""
    if not os.path.exists(config_file):
        return {}
    try:
        import yaml

        with open(config_file, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        config = {}
        auth = {}
        with open(config_file, encoding="utf-8") as f:
            in_auth = False
            for line in f:
                line = line.rstrip()
                if line.strip() == "auth:":
                    in_auth = True
                    continue
                if in_auth and line.startswith(" "):
                    m = re.match(r'\s+(\w+):\s*["\']?([^"\']+)["\']?', line)
                    if m:
                        auth[m.group(1)] = m.group(2).strip()
                elif in_auth:
                    in_auth = False
        if auth:
            config["auth"] = auth
        return config

