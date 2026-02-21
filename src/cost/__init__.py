"""Cost management for Netflix Real-Time LLM Personalization & Inference Platform.

Provides throughput-per-dollar optimization, GPU SKU evaluation,
and real-time budget tracking with alerting.
"""

from src.cost.optimizer import CostOptimizer

# These modules may not exist as separate files; import defensively.
try:
    from src.cost.budget_tracker import BudgetTracker
except ImportError:
    BudgetTracker = None  # type: ignore[assignment,misc]

try:
    from src.cost.sku_evaluator import SKUEvaluator
except ImportError:
    SKUEvaluator = None  # type: ignore[assignment,misc]

__all__ = [
    "BudgetTracker",
    "CostOptimizer",
    "SKUEvaluator",
]
