"""Netflix LLM Platform - Configuration Module.

Re-exports the settings singleton for convenient imports::

    from src.core.config import settings
"""

from src.core.config.settings import Settings, get_settings

settings = get_settings()

__all__ = ["settings", "Settings", "get_settings"]
