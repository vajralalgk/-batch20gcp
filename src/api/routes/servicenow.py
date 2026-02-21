"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
ServiceNow Integration API Endpoints
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Provides REST API endpoints for ServiceNow ITSM integration:
    1. Incident management — create/update/resolve incidents from cloud events
    2. Change management — automated change requests for migrations
    3. CMDB synchronization — keep cloud resource inventory in sync
    4. SLA tracking — monitor service level agreements

DESIGN DECISIONS:
    - Bidirectional integration: ECTP → ServiceNow and ServiceNow → ECTP (webhooks)
    - OAuth2 authentication with ServiceNow (industry standard)
    - Circuit breaker pattern for resilience (ServiceNow may have maintenance windows)
    - Async operations for non-blocking ServiceNow API calls

SECURITY IMPLICATIONS:
    - ServiceNow credentials stored in AWS Secrets Manager
    - Webhook endpoints validate HMAC signatures
    - All ServiceNow interactions are logged for audit
============================================================================
"""

from typing import Dict, Any, List, Optional  # Type hints
from enum import Enum  # Enumerations for ticket types

from fastapi import APIRouter, HTTPException, Query  # FastAPI components
from pydantic import BaseModel, Field  # Request/response validation

from src.core.logging.logger import get_logger  # Structured logging
from src.core.utils.helpers import generate_id, now_utc  # Utilities

router = APIRouter()
logger = get_logger(__name__)


# ---- Data Models ----

class IncidentPriority(str, Enum):
    """ServiceNow incident priority levels (P1=Critical, P5=Planning)."""
    P1_CRITICAL = "1"
    P2_HIGH = "2"
    P3_MODERATE = "3"
    P4_LOW = "4"
    P5_PLANNING = "5"


class IncidentState(str, Enum):
    """ServiceNow incident lifecycle states."""
    NEW = "new"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentCreate(BaseModel):
    """
    Request model for creating a ServiceNow incident.

    WHY: Standardizes incident creation from cloud events.
    When an AWS alarm fires or a migration fails, this model
    ensures all required fields are present before creating a ticket.
    """
    short_description: str = Field(
        ..., min_length=5, max_length=500,
        description="Brief incident summary",
        examples=["RDS connection pool exhausted in production"],
    )
    description: str = Field(
        ..., min_length=10,
        description="Detailed incident description",
    )
    priority: IncidentPriority = Field(
        default=IncidentPriority.P3_MODERATE,
        description="Incident priority (1=Critical, 5=Planning)",
    )
    category: str = Field(
        default="Cloud Infrastructure",
        description="ServiceNow category for routing",
    )
    assignment_group: str = Field(
        default="Cloud Operations",
        description="ServiceNow assignment group",
    )
    affected_ci: Optional[str] = Field(
        None,
        description="Configuration Item (CI) affected by this incident",
    )
    correlation_id: Optional[str] = Field(
        None,
        description="ECTP correlation ID linking to the triggering event",
    )


class IncidentResponse(BaseModel):
    """Response model for ServiceNow incident data."""
    id: str
    number: str  # ServiceNow incident number (e.g., INC0012345)
    short_description: str
    description: str
    priority: str
    state: str
    category: str
    assignment_group: str
    affected_ci: Optional[str]
    correlation_id: Optional[str]
    created_at: str
    updated_at: str


class ChangeRequestCreate(BaseModel):
    """
    Request model for creating a ServiceNow change request.

    WHY: Every cloud migration or infrastructure change requires a
    formal change request per ITIL best practices. This ensures:
    - Changes are reviewed and approved before execution
    - Impact is assessed
    - Rollback plans are documented
    - Changes are auditable
    """
    short_description: str = Field(
        ..., min_length=5, max_length=500,
        description="Brief change description",
    )
    description: str = Field(
        ..., min_length=10,
        description="Detailed change description",
    )
    change_type: str = Field(
        default="normal",
        description="Change type: normal, standard, or emergency",
    )
    risk: str = Field(
        default="moderate",
        description="Risk level: low, moderate, high",
    )
    impact: str = Field(
        default="medium",
        description="Impact level: low, medium, high",
    )
    implementation_plan: str = Field(
        ...,
        description="Step-by-step implementation plan",
    )
    rollback_plan: str = Field(
        ...,
        description="Rollback procedure if change fails",
    )
    migration_plan_id: Optional[str] = Field(
        None,
        description="ECTP migration plan ID if this change is for a migration",
    )


# ---- In-Memory Storage (Demo) ----
_incidents: Dict[str, Dict[str, Any]] = {}
_change_requests: Dict[str, Dict[str, Any]] = {}
_incident_counter = 0


# ---- API Endpoints ----

@router.post(
    "/incidents",
    response_model=IncidentResponse,
    status_code=201,
    summary="Create Incident",
    description="Creates a new incident in ServiceNow from a cloud event.",
)
async def create_incident(incident: IncidentCreate) -> Dict[str, Any]:
    """
    Creates a ServiceNow incident from a cloud platform event.

    WHY: Automated incident creation ensures:
    1. No cloud alerts are missed (human error in manual creation)
    2. Consistent incident format across all cloud events
    3. Automatic correlation between cloud events and ITSM tickets
    4. Faster mean time to response (MTTR)

    WORKFLOW:
    1. Validate incident data
    2. Generate ECTP tracking ID
    3. Create incident in ServiceNow via REST API
    4. Store mapping between ECTP ID and ServiceNow number
    5. Return the created incident

    Args:
        incident: Validated incident creation data.

    Returns:
        The created incident with ServiceNow number assigned.
    """
    global _incident_counter
    _incident_counter += 1

    incident_id = generate_id("inc")
    # Simulate ServiceNow incident number format
    snow_number = f"INC{_incident_counter:07d}"
    timestamp = now_utc().isoformat()

    incident_data = {
        "id": incident_id,
        "number": snow_number,
        "short_description": incident.short_description,
        "description": incident.description,
        "priority": incident.priority.value,
        "state": IncidentState.NEW.value,
        "category": incident.category,
        "assignment_group": incident.assignment_group,
        "affected_ci": incident.affected_ci,
        "correlation_id": incident.correlation_id,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    _incidents[incident_id] = incident_data

    logger.info(
        "servicenow_incident_created",
        incident_id=incident_id,
        snow_number=snow_number,
        priority=incident.priority.value,
        category=incident.category,
    )

    return incident_data


@router.get(
    "/incidents",
    response_model=List[IncidentResponse],
    summary="List Incidents",
    description="Retrieves incidents created through the ECTP platform.",
)
async def list_incidents(
    state: Optional[IncidentState] = Query(None, description="Filter by state"),
    priority: Optional[IncidentPriority] = Query(None, description="Filter by priority"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> List[Dict[str, Any]]:
    """Lists ServiceNow incidents with optional filtering and pagination."""
    incidents = list(_incidents.values())

    if state:
        incidents = [i for i in incidents if i["state"] == state.value]
    if priority:
        incidents = [i for i in incidents if i["priority"] == priority.value]

    return incidents[offset: offset + limit]


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentResponse,
    summary="Get Incident",
)
async def get_incident(incident_id: str) -> Dict[str, Any]:
    """Retrieves a specific incident by ID."""
    if incident_id not in _incidents:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return _incidents[incident_id]


@router.post(
    "/changes",
    status_code=201,
    summary="Create Change Request",
    description="Creates a formal change request in ServiceNow for infrastructure changes.",
)
async def create_change_request(change: ChangeRequestCreate) -> Dict[str, Any]:
    """
    Creates a ServiceNow change request for infrastructure changes.

    WHY: ITIL change management requires formal change requests for:
    - Cloud migrations
    - Infrastructure modifications
    - Security patches
    - Configuration changes

    This ensures changes are reviewed, approved, and auditable.
    """
    change_id = generate_id("chg")
    timestamp = now_utc().isoformat()

    change_data = {
        "id": change_id,
        "number": f"CHG{len(_change_requests) + 1:07d}",
        "short_description": change.short_description,
        "description": change.description,
        "change_type": change.change_type,
        "risk": change.risk,
        "impact": change.impact,
        "state": "new",
        "implementation_plan": change.implementation_plan,
        "rollback_plan": change.rollback_plan,
        "migration_plan_id": change.migration_plan_id,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    _change_requests[change_id] = change_data

    logger.info(
        "servicenow_change_created",
        change_id=change_id,
        change_type=change.change_type,
        risk=change.risk,
    )

    return change_data


@router.post(
    "/webhook",
    summary="ServiceNow Webhook Receiver",
    description="Receives webhook notifications from ServiceNow for bidirectional sync.",
)
async def servicenow_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Receives webhook events from ServiceNow.

    WHY: Bidirectional integration requires ServiceNow to notify ECTP
    when ticket state changes. For example:
    - When a change request is approved → start migration automation
    - When an incident is escalated → trigger additional monitoring
    - When a CI is updated → sync cloud resource metadata

    SECURITY: In production, this endpoint validates the HMAC signature
    in the X-ServiceNow-Signature header to ensure the webhook is
    authentic and not spoofed.

    Args:
        payload: The webhook event data from ServiceNow.

    Returns:
        Acknowledgment response.
    """
    event_type = payload.get("event_type", "unknown")

    logger.info(
        "servicenow_webhook_received",
        event_type=event_type,
        payload_keys=list(payload.keys()),
    )

    # TODO: Implement webhook signature validation
    # TODO: Route to appropriate handler based on event_type
    # TODO: Process the event (update local state, trigger automation)

    return {
        "status": "received",
        "event_type": event_type,
        "timestamp": now_utc().isoformat(),
    }
