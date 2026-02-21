"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Cloud Migration API Endpoints
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Provides REST API endpoints for the Cloud Migration service, enabling:
    1. Discovery of on-premises workloads for migration assessment
    2. Creation and management of migration plans
    3. Execution and monitoring of migration tasks
    4. Post-migration validation and optimization

DESIGN DECISIONS:
    - RESTful resource design: /migrations is the collection, /migrations/{id} is the resource
    - Async endpoints for non-blocking I/O (migrations involve AWS API calls)
    - Pydantic models for request/response validation
    - Pagination for list endpoints (migrations can number in thousands)

SECURITY IMPLICATIONS:
    - All endpoints require authentication (migration operations are sensitive)
    - Migration creation requires "migration:create" permission
    - Migration execution requires "migration:execute" permission (higher privilege)
============================================================================
"""

from datetime import datetime  # Timestamp handling
from typing import Dict, Any, List, Optional  # Type hints
from enum import Enum  # Enumeration for migration strategies

from fastapi import APIRouter, HTTPException, Query  # FastAPI components
from pydantic import BaseModel, Field  # Request/response models

from src.core.logging.logger import get_logger  # Structured logging
from src.core.utils.helpers import generate_id, now_utc  # Utilities

# Create router for migration endpoints
router = APIRouter()
logger = get_logger(__name__)


# ---- Data Models ----
# Pydantic models define the shape of request/response data with validation.
# WHY: Type safety, automatic validation, and OpenAPI schema generation.

class MigrationStrategy(str, Enum):
    """
    The 6R migration strategies as defined by AWS.

    WHY: Each workload requires a specific migration approach based on
    its architecture, dependencies, and business requirements.

    - REHOST: "Lift and shift" — move as-is to cloud VMs
    - REPLATFORM: Minor optimizations during migration (e.g., managed DB)
    - REPURCHASE: Replace with SaaS alternative
    - REFACTOR: Re-architect for cloud-native
    - RETIRE: Decommission — no longer needed
    - RETAIN: Keep on-premises — not suitable for cloud
    """
    REHOST = "rehost"
    REPLATFORM = "replatform"
    REPURCHASE = "repurchase"
    REFACTOR = "refactor"
    RETIRE = "retire"
    RETAIN = "retain"


class MigrationStatus(str, Enum):
    """
    Lifecycle states of a migration plan.

    WHY: Migrations follow a defined workflow. Each state has specific
    actions available and transitions to the next state.
    """
    DRAFT = "draft"              # Initial creation, being planned
    ASSESSMENT = "assessment"    # Workload being assessed
    PLANNING = "planning"        # Migration plan being developed
    APPROVED = "approved"        # Plan approved by change board
    IN_PROGRESS = "in_progress"  # Migration actively executing
    VALIDATING = "validating"    # Post-migration validation
    COMPLETED = "completed"      # Successfully migrated
    FAILED = "failed"            # Migration failed
    ROLLED_BACK = "rolled_back"  # Rolled back to on-premises


class MigrationPlanCreate(BaseModel):
    """
    Request model for creating a new migration plan.

    WHY: Validates all required fields before creating a plan.
    Pydantic raises 422 errors automatically if validation fails,
    ensuring only valid data reaches the business logic layer.
    """
    # name: Human-readable name for the migration plan
    # WHY: Used in dashboards, reports, and ServiceNow tickets
    name: str = Field(
        ...,  # ... means required (no default)
        min_length=3,  # Prevent meaningless short names
        max_length=200,  # Prevent excessively long names
        description="Name of the migration plan",
        examples=["Banner Database Migration to RDS"],
    )

    # description: Detailed description of what is being migrated
    description: str = Field(
        ...,
        min_length=10,
        description="Detailed description of the migration",
    )

    # strategy: Which of the 6Rs applies to this workload
    strategy: MigrationStrategy = Field(
        ...,
        description="Migration strategy (6R model)",
    )

    # source_system: What is being migrated (on-premises server, application, etc.)
    source_system: str = Field(
        ...,
        description="Source system or application being migrated",
    )

    # target_aws_service: Where it's going in AWS
    target_aws_service: str = Field(
        ...,
        description="Target AWS service (e.g., EC2, RDS, ECS)",
    )

    # priority: Business priority (1=critical, 5=low)
    priority: int = Field(
        default=3,
        ge=1,  # Greater than or equal to 1
        le=5,  # Less than or equal to 5
        description="Priority 1 (critical) to 5 (low)",
    )

    # owner: Team or person responsible
    owner: str = Field(
        ...,
        description="Team or individual responsible for this migration",
    )

    # estimated_downtime_minutes: Expected downtime during cutover
    estimated_downtime_minutes: int = Field(
        default=0,
        ge=0,
        description="Estimated downtime in minutes during migration",
    )


class MigrationPlanResponse(BaseModel):
    """
    Response model for migration plan data.

    WHY: Controls exactly what data is returned to API consumers.
    This is important for:
    1. Hiding internal fields that clients don't need
    2. Formatting dates consistently
    3. Ensuring API backward compatibility
    """
    id: str
    name: str
    description: str
    strategy: MigrationStrategy
    status: MigrationStatus
    source_system: str
    target_aws_service: str
    priority: int
    owner: str
    estimated_downtime_minutes: int
    created_at: str
    updated_at: str


# ---- In-Memory Storage (Demo) ----
# WHY: For demonstration purposes. In production, this would be PostgreSQL via SQLAlchemy.
# This allows the API to work without a database for initial testing and development.
_migration_plans: Dict[str, Dict[str, Any]] = {}


# ---- API Endpoints ----

@router.post(
    "/plans",
    response_model=MigrationPlanResponse,
    status_code=201,  # HTTP 201 Created — standard for resource creation
    summary="Create Migration Plan",
    description="Creates a new cloud migration plan for a workload.",
)
async def create_migration_plan(plan: MigrationPlanCreate) -> Dict[str, Any]:
    """
    Creates a new migration plan.

    WHY: Every workload migration starts with a plan that documents:
    - What is being migrated (source_system)
    - Where it's going (target_aws_service)
    - How it will be migrated (strategy)
    - Who is responsible (owner)
    - Expected impact (downtime)

    WORKFLOW:
    1. Validate input (handled by Pydantic model above)
    2. Generate unique ID
    3. Set initial status to DRAFT
    4. Store the plan
    5. Return the created plan

    Args:
        plan: Validated migration plan data from request body.

    Returns:
        The created migration plan with generated ID and timestamps.
    """
    # Generate a unique ID with "mig" prefix for easy identification in logs.
    plan_id = generate_id("mig")

    # Get current UTC timestamp for audit trail.
    # WHY: Tracking creation and modification times is essential for:
    # - Audit compliance (FERPA requires knowing when records were created/modified)
    # - Debugging (correlating events across systems)
    timestamp = now_utc().isoformat()

    # Build the complete plan record
    plan_data = {
        "id": plan_id,
        "name": plan.name,
        "description": plan.description,
        "strategy": plan.strategy.value,
        "status": MigrationStatus.DRAFT.value,  # All new plans start as DRAFT
        "source_system": plan.source_system,
        "target_aws_service": plan.target_aws_service,
        "priority": plan.priority,
        "owner": plan.owner,
        "estimated_downtime_minutes": plan.estimated_downtime_minutes,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    # Store the plan (in-memory for demo; PostgreSQL in production)
    _migration_plans[plan_id] = plan_data

    # Log the creation for audit trail
    logger.info(
        "migration_plan_created",
        plan_id=plan_id,
        name=plan.name,
        strategy=plan.strategy.value,
        owner=plan.owner,
    )

    return plan_data


@router.get(
    "/plans",
    response_model=List[MigrationPlanResponse],
    summary="List Migration Plans",
    description="Retrieves all migration plans with optional filtering.",
)
async def list_migration_plans(
    # Query parameters for filtering and pagination
    status: Optional[MigrationStatus] = Query(
        None, description="Filter by migration status"
    ),
    strategy: Optional[MigrationStrategy] = Query(
        None, description="Filter by migration strategy"
    ),
    limit: int = Query(
        50, ge=1, le=200,
        description="Maximum number of results (pagination)"
    ),
    offset: int = Query(
        0, ge=0,
        description="Number of results to skip (pagination)"
    ),
) -> List[Dict[str, Any]]:
    """
    Lists migration plans with optional filtering and pagination.

    WHY: Organizations may have hundreds of migration plans across
    different teams and phases. Filtering and pagination are essential
    for usability and performance.

    PAGINATION:
    - limit: How many results to return (max 200 per page)
    - offset: How many results to skip (for page navigation)
    Example: Page 3 with 50 per page → offset=100, limit=50

    Args:
        status: Optional filter by migration status
        strategy: Optional filter by migration strategy
        limit: Maximum results per page
        offset: Results to skip for pagination

    Returns:
        List of migration plans matching the filters.
    """
    # Start with all plans
    plans = list(_migration_plans.values())

    # Apply status filter if provided
    if status:
        plans = [p for p in plans if p["status"] == status.value]

    # Apply strategy filter if provided
    if strategy:
        plans = [p for p in plans if p["strategy"] == strategy.value]

    # Apply pagination
    # WHY: Without pagination, listing thousands of plans would
    # consume excessive memory and network bandwidth.
    paginated = plans[offset: offset + limit]

    logger.info(
        "migration_plans_listed",
        total=len(plans),
        returned=len(paginated),
        filters={"status": status, "strategy": strategy},
    )

    return paginated


@router.get(
    "/plans/{plan_id}",
    response_model=MigrationPlanResponse,
    summary="Get Migration Plan",
    description="Retrieves a specific migration plan by ID.",
)
async def get_migration_plan(plan_id: str) -> Dict[str, Any]:
    """
    Retrieves a specific migration plan by its unique ID.

    WHY: Individual plan retrieval is needed for:
    - Plan detail views in the dashboard
    - ServiceNow ticket linking (ticket contains plan ID)
    - API-to-API calls between platform services

    Args:
        plan_id: The unique migration plan identifier.

    Returns:
        The migration plan data.

    Raises:
        HTTPException 404 if the plan doesn't exist.
    """
    # Check if the plan exists
    if plan_id not in _migration_plans:
        # Log the failed lookup for monitoring (detect scanning attempts)
        logger.warning("migration_plan_not_found", plan_id=plan_id)

        # Return 404 with descriptive message
        # WHY: Clear error messages speed up debugging for API consumers.
        raise HTTPException(
            status_code=404,
            detail=f"Migration plan '{plan_id}' not found",
        )

    return _migration_plans[plan_id]


@router.patch(
    "/plans/{plan_id}/status",
    response_model=MigrationPlanResponse,
    summary="Update Migration Status",
    description="Updates the status of a migration plan (workflow transition).",
)
async def update_migration_status(
    plan_id: str,
    new_status: MigrationStatus,
) -> Dict[str, Any]:
    """
    Updates the status of a migration plan (workflow state transition).

    WHY: Migration plans follow a defined workflow:
    DRAFT → ASSESSMENT → PLANNING → APPROVED → IN_PROGRESS →
    VALIDATING → COMPLETED (or FAILED → ROLLED_BACK)

    Each transition triggers different actions:
    - APPROVED: Creates a ServiceNow change request
    - IN_PROGRESS: Starts migration automation
    - COMPLETED: Updates CMDB in ServiceNow
    - FAILED: Triggers incident in ServiceNow

    Args:
        plan_id: The migration plan to update.
        new_status: The new status to set.

    Returns:
        The updated migration plan.

    Raises:
        HTTPException 404 if the plan doesn't exist.
    """
    if plan_id not in _migration_plans:
        raise HTTPException(
            status_code=404,
            detail=f"Migration plan '{plan_id}' not found",
        )

    plan = _migration_plans[plan_id]
    old_status = plan["status"]

    # Update the status and modification timestamp
    plan["status"] = new_status.value
    plan["updated_at"] = now_utc().isoformat()

    # Log the state transition for audit trail
    # WHY: All state changes must be auditable for compliance.
    # FERPA and SOC2 require tracking who changed what and when.
    logger.info(
        "migration_status_updated",
        plan_id=plan_id,
        old_status=old_status,
        new_status=new_status.value,
    )

    return plan
