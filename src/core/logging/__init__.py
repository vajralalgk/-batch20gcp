"""
============================================================================
ECTP Logging Module
Author: Gopi Krishna Vajrala
============================================================================
Re-exports the logging setup for convenient imports:
    from src.core.logging import get_logger, setup_logging
============================================================================
"""

from src.core.logging.logger import get_logger, setup_logging

__all__ = ["get_logger", "setup_logging"]
