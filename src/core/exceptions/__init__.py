"""
============================================================================
ECTP Custom Exceptions Module
Author: Gopi Krishna Vajrala
============================================================================
Re-exports all custom exceptions for convenient imports:
    from src.core.exceptions import NotFoundError, AuthenticationError
============================================================================
"""

from src.core.exceptions.handlers import (
    ECTPBaseError,
    NotFoundError,
    AuthenticationError,
    AuthorizationError,
    ValidationError,
    ExternalServiceError,
    ServiceNowError,
    EllucianError,
    AWSServiceError,
    RateLimitError,
    ConfigurationError,
)

__all__ = [
    "ECTPBaseError",
    "NotFoundError",
    "AuthenticationError",
    "AuthorizationError",
    "ValidationError",
    "ExternalServiceError",
    "ServiceNowError",
    "EllucianError",
    "AWSServiceError",
    "RateLimitError",
    "ConfigurationError",
]
