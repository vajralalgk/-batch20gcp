# Netflix LLM Personalization Platform - API Routes Package
# Author: Gopi Krishna Vajrala

from src.api.routes import health
from src.api.routes import inference
from src.api.routes import personalization
from src.api.routes import gpu
from src.api.routes import cost
from src.api.routes import control

__all__ = [
    "health",
    "inference",
    "personalization",
    "gpu",
    "cost",
    "control",
]
