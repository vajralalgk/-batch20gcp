"""Netflix LLM Platform - Core Package.

Contains shared modules used across all platform services:
  - config:     Centralized configuration management via pydantic-settings
  - logging:    Structured JSON logging with GPU context fields
  - exceptions: Custom exception hierarchy and FastAPI error handlers
  - utils:      Shared utility functions (timing, retry, token counting)
"""
