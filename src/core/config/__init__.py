# ============================================================================
# ECTP Configuration Module
# Author: Gopi Krishna Vajrala
# ============================================================================
# Re-exports the settings singleton for convenient imports:
#   from src.core.config import settings
# ============================================================================

from src.core.config.settings import Settings, get_settings

# Create a module-level settings instance so other modules can import it directly.
# Using get_settings() ensures the singleton pattern - only one Settings object exists.
settings = get_settings()

__all__ = ["settings", "Settings", "get_settings"]
