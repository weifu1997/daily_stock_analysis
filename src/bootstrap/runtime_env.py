# -*- coding: utf-8 -*-
"""Bootstrap helpers for runtime env loading and scheduled config refresh."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional, Set

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_INITIAL_PROCESS_ENV = dict(os.environ)
_RUNTIME_ENV_FILE_KEYS: Set[str] = set()


def get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parents[2] / ".env"


def read_active_env_values() -> Optional[Dict[str, str]]:
    env_path = get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("读取配置文件 %s 失败，继续沿用当前环境变量: %s", env_path, exc)
        return None

    return {
        str(key): "" if value is None else str(value)
        for key, value in values.items()
        if key is not None
    }


_ACTIVE_ENV_FILE_VALUES = read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {
    key for key in _ACTIVE_ENV_FILE_VALUES if key not in _INITIAL_PROCESS_ENV
}


def bootstrap_runtime_environment() -> None:
    """Load env and apply optional proxy settings once."""
    global _INITIAL_PROCESS_ENV
    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    # Keep a stable snapshot for later scheduled refresh semantics.
    _INITIAL_PROCESS_ENV = dict(os.environ)


def reload_env_file_values_preserving_overrides() -> None:
    """Refresh .env-managed env vars without clobbering process overrides."""
    global _RUNTIME_ENV_FILE_KEYS

    latest_values = read_active_env_values()
    if latest_values is None:
        return

    managed_keys = {key for key in latest_values if key not in _INITIAL_PROCESS_ENV}

    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)

    for key in managed_keys:
        os.environ[key] = latest_values[key]

    _RUNTIME_ENV_FILE_KEYS = managed_keys


def reload_runtime_config():
    """Reload config from the latest persisted .env values."""
    from src.config import Config, get_config

    reload_env_file_values_preserving_overrides()
    Config.reset_instance()
    return get_config()


def is_runtime_env_key_managed(key: str) -> bool:
    return key in _RUNTIME_ENV_FILE_KEYS


def set_runtime_env_keys(keys: Set[str]) -> None:
    global _RUNTIME_ENV_FILE_KEYS
    _RUNTIME_ENV_FILE_KEYS = set(keys)


def get_initial_process_env() -> Dict[str, str]:
    return dict(_INITIAL_PROCESS_ENV)


def build_schedule_time_provider(default_schedule_time: str):
    """Read latest schedule time from process env or persisted config file."""
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SCHEDULE_TIME = "18:00"
    manager = ConfigManager()

    def _provider() -> str:
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("SCHEDULE_TIME", default_schedule_time)

        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip()
        if schedule_time:
            return schedule_time
        return _SYSTEM_DEFAULT_SCHEDULE_TIME

    return _provider
