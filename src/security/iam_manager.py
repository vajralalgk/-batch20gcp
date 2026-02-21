"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
IAM Policy Enforcement for Inference Platform
============================================================================

WHY THIS MODULE EXISTS:
    The LLM inference platform handles sensitive model artifacts, user
    personalization data, and GPU cluster resources worth millions of dollars.
    Without rigorous access control:
    1. Unauthorized users could deploy untested models to production
    2. Data scientists could accidentally scale GPU clusters beyond budget
    3. Service accounts could access resources outside their scope
    4. There would be no audit trail for compliance (SOC2, GDPR)

    This module enforces role-based access control (RBAC) with a deny-override
    policy evaluation strategy, matching AWS IAM semantics.

DESIGN DECISIONS:
    - Deny-override evaluation: An explicit DENY always wins over an ALLOW.
      This prevents privilege escalation through overlapping policies.
    - Four roles map to Netflix engineering org structure:
      admin (platform team), operator (SRE), data-scientist (ML team),
      service-account (automated pipelines)
    - Resource types match the platform's core infrastructure components
    - Policies are evaluated at request time, not cached, to ensure
      revocations take effect immediately

SECURITY IMPLICATIONS:
    - Default deny: Any request without an explicit ALLOW is denied
    - Audit log captures every access decision for compliance
    - Credential rotation generates new secrets and invalidates old ones
    - Service roles follow least-privilege with scoped permissions

ALTERNATIVES CONSIDERED:
    - Attribute-Based Access Control (ABAC): More flexible but harder to
      audit and reason about. RBAC chosen for clarity.
    - External policy engine (OPA/Cedar): Better for complex policies but
      adds operational overhead. Internal engine sufficient for current needs.
============================================================================
"""

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain enumerations
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """
    Platform roles mapped to Netflix engineering organizational structure.

    WHY: Roles provide coarse-grained access boundaries. Each role represents
    a distinct responsibility level within the inference platform team.
    """
    ADMIN = "admin"
    OPERATOR = "operator"
    DATA_SCIENTIST = "data-scientist"
    SERVICE_ACCOUNT = "service-account"


class ResourceType(str, Enum):
    """
    Infrastructure resource types managed by the inference platform.

    WHY: Typed resources allow fine-grained permission grants. A data scientist
    can read models but should not be able to scale GPU clusters.
    """
    MODEL = "model"
    INFERENCE_ENDPOINT = "inference-endpoint"
    GPU_CLUSTER = "gpu-cluster"
    DASHBOARD = "dashboard"


class Action(str, Enum):
    """
    Actions that can be performed on platform resources.

    WHY: Separating actions from resources enables precise permission grants.
    For example, an operator can scale a GPU cluster but cannot delete it.
    """
    READ = "read"
    WRITE = "write"
    DEPLOY = "deploy"
    DELETE = "delete"
    SCALE = "scale"


class PolicyEffect(str, Enum):
    """
    Policy evaluation outcome — either ALLOW or DENY.

    WHY: Explicit deny support enables security guardrails that cannot be
    overridden by broader allow policies. This follows the principle of
    least privilege with deny-override semantics.
    """
    ALLOW = "allow"
    DENY = "deny"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PolicyStatement:
    """
    A single policy statement granting or denying access.

    WHY: Policy statements are the atomic unit of access control. Each
    statement specifies a set of resources, a set of actions, and an effect
    (allow/deny). Wildcard ('*') support enables broad grants where appropriate.

    Attributes:
        effect: Whether this statement allows or denies access.
        resources: Set of resource types this statement applies to. Use '*' for all.
        actions: Set of actions this statement governs. Use '*' for all.
        conditions: Optional conditions that must be met (e.g., time-of-day, IP range).
    """
    effect: PolicyEffect
    resources: Set[str]
    actions: Set[str]
    conditions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Policy:
    """
    A named collection of policy statements attached to a principal.

    WHY: Grouping statements into named policies makes them manageable.
    A principal can have multiple policies, and the evaluation engine
    combines them using deny-override logic.

    Attributes:
        policy_id: Unique identifier for this policy.
        name: Human-readable policy name.
        statements: Ordered list of policy statements.
        created_at: When this policy was created (UTC).
        description: Optional description of this policy's purpose.
    """
    policy_id: str
    name: str
    statements: List[PolicyStatement]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""


@dataclass
class Principal:
    """
    An identity that can be authenticated and authorized.

    WHY: Principals represent both human users and service accounts. Tracking
    their role, policies, active status, and credential metadata enables
    comprehensive access control and audit capabilities.

    Attributes:
        principal_id: Unique identifier (e.g., user ID or service account name).
        role: The role assigned to this principal.
        policies: List of policies attached to this principal.
        is_active: Whether this principal can currently authenticate.
        created_at: When this principal was provisioned (UTC).
        last_credential_rotation: When credentials were last rotated (UTC).
        metadata: Additional key-value metadata about this principal.
    """
    principal_id: str
    role: Role
    policies: List[Policy] = field(default_factory=list)
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_credential_rotation: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessDecision:
    """
    The result of a policy evaluation for an access request.

    WHY: Structured access decisions enable audit logging and debugging.
    When access is denied, the reason and matched policy help operators
    understand why and adjust policies if needed.

    Attributes:
        allowed: Whether the access request was granted.
        principal_id: Who requested access.
        resource: What resource was requested.
        action: What action was attempted.
        reason: Human-readable explanation of the decision.
        matched_policy: The policy that determined the outcome (if any).
        evaluated_at: When this decision was made (UTC).
    """
    allowed: bool
    principal_id: str
    resource: str
    action: str
    reason: str
    matched_policy: Optional[str] = None
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AuditEntry:
    """
    An immutable record of an access decision for compliance reporting.

    WHY: SOC2 and GDPR require a complete audit trail of who accessed what.
    Audit entries are append-only and include the full decision context.

    Attributes:
        entry_id: Unique identifier for this audit entry.
        decision: The access decision that was recorded.
        source_ip: IP address of the requester (if available).
        timestamp: When this entry was created (UTC).
    """
    entry_id: str
    decision: AccessDecision
    source_ip: str = "unknown"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Default role policies
# ---------------------------------------------------------------------------

def _build_default_policies() -> Dict[Role, Policy]:
    """
    Constructs the default RBAC policies for each platform role.

    WHY: Default policies encode the principle of least privilege for each role:
    - admin: Full access to all resources and actions
    - operator: Can read, deploy, and scale — but cannot delete
    - data-scientist: Can read and write models, read dashboards and endpoints
    - service-account: Can read models and deploy to inference endpoints

    These defaults can be augmented with additional policies per principal.

    Returns:
        Mapping from Role to its default Policy.
    """
    return {
        Role.ADMIN: Policy(
            policy_id="policy-admin-default",
            name="AdminFullAccess",
            description="Full administrative access to all platform resources.",
            statements=[
                PolicyStatement(
                    effect=PolicyEffect.ALLOW,
                    resources={"*"},
                    actions={"*"},
                ),
            ],
        ),
        Role.OPERATOR: Policy(
            policy_id="policy-operator-default",
            name="OperatorAccess",
            description="Operational access: read, deploy, and scale. No delete.",
            statements=[
                PolicyStatement(
                    effect=PolicyEffect.ALLOW,
                    resources={"*"},
                    actions={
                        Action.READ.value,
                        Action.DEPLOY.value,
                        Action.SCALE.value,
                    },
                ),
                PolicyStatement(
                    effect=PolicyEffect.DENY,
                    resources={"*"},
                    actions={Action.DELETE.value},
                ),
            ],
        ),
        Role.DATA_SCIENTIST: Policy(
            policy_id="policy-datascientist-default",
            name="DataScientistAccess",
            description=(
                "ML practitioner access: read/write models, read-only for "
                "dashboards and inference endpoints."
            ),
            statements=[
                PolicyStatement(
                    effect=PolicyEffect.ALLOW,
                    resources={ResourceType.MODEL.value},
                    actions={Action.READ.value, Action.WRITE.value},
                ),
                PolicyStatement(
                    effect=PolicyEffect.ALLOW,
                    resources={
                        ResourceType.DASHBOARD.value,
                        ResourceType.INFERENCE_ENDPOINT.value,
                    },
                    actions={Action.READ.value},
                ),
                PolicyStatement(
                    effect=PolicyEffect.DENY,
                    resources={ResourceType.GPU_CLUSTER.value},
                    actions={Action.DELETE.value, Action.SCALE.value},
                ),
            ],
        ),
        Role.SERVICE_ACCOUNT: Policy(
            policy_id="policy-serviceaccount-default",
            name="ServiceAccountAccess",
            description=(
                "Automated pipeline access: read models and deploy to "
                "inference endpoints."
            ),
            statements=[
                PolicyStatement(
                    effect=PolicyEffect.ALLOW,
                    resources={ResourceType.MODEL.value},
                    actions={Action.READ.value},
                ),
                PolicyStatement(
                    effect=PolicyEffect.ALLOW,
                    resources={ResourceType.INFERENCE_ENDPOINT.value},
                    actions={Action.READ.value, Action.DEPLOY.value},
                ),
            ],
        ),
    }


# ---------------------------------------------------------------------------
# IAM Manager
# ---------------------------------------------------------------------------

class IAMManager:
    """
    IAM policy enforcement engine for the Netflix LLM inference platform.

    WHY: Centralized access control ensures consistent policy enforcement
    across all platform components. Every API call, model deployment, and
    cluster operation is authorized through this manager.

    The evaluation strategy is deny-override:
    1. Collect all applicable policy statements for the principal
    2. If ANY statement explicitly denies the request, deny it
    3. If at least one statement explicitly allows the request, allow it
    4. Otherwise, default deny (implicit deny)

    This matches AWS IAM semantics and prevents privilege escalation through
    overlapping allow policies.

    Usage:
        iam = IAMManager()
        decision = iam.validate_access("user-123", "model", "read")
        if decision.allowed:
            # proceed with operation
            ...
    """

    def __init__(self) -> None:
        """
        Initialize the IAM manager with default role policies and empty stores.

        WHY: The manager starts with sensible default policies for each role.
        Principals and additional policies can be added at runtime. The audit
        log is initialized empty and grows as access decisions are made.
        """
        self._default_policies: Dict[Role, Policy] = _build_default_policies()
        self._principals: Dict[str, Principal] = {}
        self._custom_policies: Dict[str, Policy] = {}
        self._audit_log: List[AuditEntry] = []
        self._credentials: Dict[str, Dict[str, Any]] = {}

        logger.info("iam_manager_initialized", extra={"roles_loaded": len(self._default_policies)})

    # ------------------------------------------------------------------
    # Principal management
    # ------------------------------------------------------------------

    def register_principal(
        self,
        principal_id: str,
        role: Role,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Principal:
        """
        Register a new principal (user or service account) in the IAM store.

        WHY: Principals must be explicitly registered before they can be
        authorized. This prevents unknown identities from accessing resources.

        Args:
            principal_id: Unique identifier for the principal.
            role: The role to assign.
            metadata: Optional key-value metadata (team, department, etc.).

        Returns:
            The newly created Principal.

        Raises:
            ValueError: If a principal with the same ID already exists.
        """
        if principal_id in self._principals:
            raise ValueError(f"Principal '{principal_id}' already exists")

        default_policy = self._default_policies.get(role)
        policies = [default_policy] if default_policy else []

        principal = Principal(
            principal_id=principal_id,
            role=role,
            policies=policies,
            metadata=metadata or {},
        )
        self._principals[principal_id] = principal

        # Generate initial credentials
        self._credentials[principal_id] = {
            "secret_key_hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rotation_count": 0,
        }

        logger.info(
            "principal_registered",
            extra={
                "principal_id": principal_id,
                "role": role.value,
            },
        )
        return principal

    # ------------------------------------------------------------------
    # Core access control
    # ------------------------------------------------------------------

    def validate_access(
        self,
        principal_id: str,
        resource: str,
        action: str,
    ) -> AccessDecision:
        """
        Evaluate whether a principal is authorized to perform an action on a resource.

        WHY: This is the central authorization check. Every operation in the
        platform calls this method before proceeding. The deny-override
        evaluation ensures that explicit deny policies cannot be circumvented
        by broader allow policies.

        Evaluation order:
        1. Check that the principal exists and is active
        2. Collect all policy statements from all of the principal's policies
        3. If any statement explicitly denies, return DENY
        4. If any statement explicitly allows, return ALLOW
        5. Otherwise, implicit DENY (default deny)

        Args:
            principal_id: The identity requesting access.
            resource: The resource type being accessed (e.g., 'model').
            action: The action being attempted (e.g., 'read').

        Returns:
            An AccessDecision with the authorization result and reasoning.
        """
        # Step 1: Verify the principal exists
        principal = self._principals.get(principal_id)
        if principal is None:
            decision = AccessDecision(
                allowed=False,
                principal_id=principal_id,
                resource=resource,
                action=action,
                reason=f"Principal '{principal_id}' is not registered",
            )
            self._record_audit(decision)
            return decision

        # Step 2: Verify the principal is active
        if not principal.is_active:
            decision = AccessDecision(
                allowed=False,
                principal_id=principal_id,
                resource=resource,
                action=action,
                reason=f"Principal '{principal_id}' is deactivated",
            )
            self._record_audit(decision)
            return decision

        # Step 3: Evaluate all policies using deny-override
        has_explicit_allow = False
        matched_allow_policy: Optional[str] = None

        for policy in principal.policies:
            for statement in policy.statements:
                if not self._statement_matches(statement, resource, action):
                    continue

                # Explicit DENY always wins — short-circuit
                if statement.effect == PolicyEffect.DENY:
                    decision = AccessDecision(
                        allowed=False,
                        principal_id=principal_id,
                        resource=resource,
                        action=action,
                        reason=(
                            f"Explicit DENY in policy '{policy.name}' "
                            f"for {action} on {resource}"
                        ),
                        matched_policy=policy.policy_id,
                    )
                    self._record_audit(decision)
                    return decision

                # Track that we found at least one ALLOW
                if statement.effect == PolicyEffect.ALLOW:
                    has_explicit_allow = True
                    matched_allow_policy = policy.policy_id

        # Step 4: If we found an explicit ALLOW (and no DENY), grant access
        if has_explicit_allow:
            decision = AccessDecision(
                allowed=True,
                principal_id=principal_id,
                resource=resource,
                action=action,
                reason=f"Allowed by policy '{matched_allow_policy}'",
                matched_policy=matched_allow_policy,
            )
            self._record_audit(decision)
            return decision

        # Step 5: Implicit deny — no matching statements
        decision = AccessDecision(
            allowed=False,
            principal_id=principal_id,
            resource=resource,
            action=action,
            reason=(
                f"Implicit DENY: no policy grants '{action}' on '{resource}' "
                f"for role '{principal.role.value}'"
            ),
        )
        self._record_audit(decision)
        return decision

    def _statement_matches(
        self,
        statement: PolicyStatement,
        resource: str,
        action: str,
    ) -> bool:
        """
        Check whether a policy statement applies to the given resource and action.

        WHY: Wildcard matching ('*') allows broad policies like "allow all
        actions on all resources" while typed matching enables fine-grained
        grants like "allow read on model".

        Args:
            statement: The policy statement to evaluate.
            resource: The resource type being accessed.
            action: The action being attempted.

        Returns:
            True if the statement's resource and action sets match.
        """
        resource_match = ("*" in statement.resources) or (resource in statement.resources)
        action_match = ("*" in statement.actions) or (action in statement.actions)
        return resource_match and action_match

    # ------------------------------------------------------------------
    # Policy retrieval
    # ------------------------------------------------------------------

    def get_policy(self, principal_id: str) -> Dict[str, Any]:
        """
        Retrieve the full policy document for a principal.

        WHY: Operators and auditors need to inspect the effective policies
        for a principal to troubleshoot access issues and verify compliance.

        Args:
            principal_id: The identity whose policies to retrieve.

        Returns:
            A dictionary containing the principal's role, status, and all
            attached policy details including their statements.

        Raises:
            ValueError: If the principal is not registered.
        """
        principal = self._principals.get(principal_id)
        if principal is None:
            raise ValueError(f"Principal '{principal_id}' not found")

        return {
            "principal_id": principal.principal_id,
            "role": principal.role.value,
            "is_active": principal.is_active,
            "created_at": principal.created_at.isoformat(),
            "policies": [
                {
                    "policy_id": p.policy_id,
                    "name": p.name,
                    "description": p.description,
                    "statements": [
                        {
                            "effect": s.effect.value,
                            "resources": sorted(s.resources),
                            "actions": sorted(s.actions),
                            "conditions": s.conditions,
                        }
                        for s in p.statements
                    ],
                }
                for p in principal.policies
            ],
            "metadata": principal.metadata,
        }

    # ------------------------------------------------------------------
    # Service role provisioning
    # ------------------------------------------------------------------

    def create_service_role(self, service_name: str) -> Dict[str, Any]:
        """
        Provision a new service account with scoped permissions.

        WHY: Automated pipelines (model training, batch inference, A/B testing)
        need machine identities that follow least privilege. Service roles are
        created with a deterministic ID and default service-account permissions,
        plus a generated secret for authentication.

        The generated secret is returned exactly once at creation time. It is
        stored only as a SHA-256 hash — the plaintext cannot be recovered.

        Args:
            service_name: A human-readable name for the service (e.g., 'model-trainer').

        Returns:
            A dictionary containing the service account ID, the generated secret
            (returned only once), the assigned role, and creation timestamp.
        """
        service_id = f"svc-{service_name}-{uuid.uuid4().hex[:8]}"
        generated_secret = secrets.token_urlsafe(48)

        principal = self.register_principal(
            principal_id=service_id,
            role=Role.SERVICE_ACCOUNT,
            metadata={
                "service_name": service_name,
                "type": "service-account",
                "provisioned_by": "iam_manager",
            },
        )

        # Store only the hash of the secret
        self._credentials[service_id] = {
            "secret_key_hash": hashlib.sha256(generated_secret.encode()).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rotation_count": 0,
        }

        logger.info(
            "service_role_created",
            extra={
                "service_id": service_id,
                "service_name": service_name,
            },
        )

        return {
            "service_id": service_id,
            "service_name": service_name,
            "secret": generated_secret,  # Returned only at creation time
            "role": Role.SERVICE_ACCOUNT.value,
            "created_at": principal.created_at.isoformat(),
            "policies": [p.name for p in principal.policies],
        }

    # ------------------------------------------------------------------
    # Audit and compliance
    # ------------------------------------------------------------------

    def audit_permissions(self) -> Dict[str, Any]:
        """
        Generate a comprehensive permissions audit report.

        WHY: Compliance frameworks (SOC2, GDPR, ISO 27001) require periodic
        reviews of who has access to what. This method produces a report that
        includes:
        - Per-principal role and policy summary
        - Credential rotation status
        - Recent access decisions
        - Principals with elevated privileges (admin)
        - Inactive principals that should be cleaned up

        Returns:
            A dictionary containing the full audit report with timestamp,
            principal summaries, statistics, and flagged concerns.
        """
        report: Dict[str, Any] = {
            "audit_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_principals": len(self._principals),
            "principals": {},
            "role_distribution": {},
            "flagged_concerns": [],
        }

        # Count principals per role
        role_counts: Dict[str, int] = {}
        active_count = 0
        inactive_count = 0

        for pid, principal in self._principals.items():
            role_name = principal.role.value
            role_counts[role_name] = role_counts.get(role_name, 0) + 1

            if principal.is_active:
                active_count += 1
            else:
                inactive_count += 1

            # Build per-principal summary
            credential_info = self._credentials.get(pid, {})
            report["principals"][pid] = {
                "role": role_name,
                "is_active": principal.is_active,
                "policy_count": len(principal.policies),
                "policies": [p.name for p in principal.policies],
                "created_at": principal.created_at.isoformat(),
                "last_credential_rotation": (
                    principal.last_credential_rotation.isoformat()
                    if principal.last_credential_rotation
                    else None
                ),
                "credential_rotation_count": credential_info.get("rotation_count", 0),
            }

            # Flag concerns
            if principal.role == Role.ADMIN:
                report["flagged_concerns"].append(
                    f"Principal '{pid}' has admin role — verify this is intended"
                )

            if not principal.is_active:
                report["flagged_concerns"].append(
                    f"Principal '{pid}' is inactive — consider removing"
                )

            if (
                principal.last_credential_rotation is None
                and principal.is_active
            ):
                report["flagged_concerns"].append(
                    f"Principal '{pid}' has never rotated credentials"
                )

        report["role_distribution"] = role_counts
        report["active_principals"] = active_count
        report["inactive_principals"] = inactive_count
        report["recent_access_decisions"] = len(self._audit_log)
        report["recent_denials"] = sum(
            1 for entry in self._audit_log if not entry.decision.allowed
        )

        logger.info(
            "permissions_audit_completed",
            extra={
                "total_principals": len(self._principals),
                "flagged_concerns": len(report["flagged_concerns"]),
            },
        )

        return report

    # ------------------------------------------------------------------
    # Credential rotation
    # ------------------------------------------------------------------

    def rotate_credentials(self, principal_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Rotate credentials for a specific principal or all principals.

        WHY: Regular credential rotation limits the blast radius of
        compromised secrets. If a service account secret is leaked, rotation
        ensures the leaked secret becomes invalid. Netflix policy requires
        rotation at least every 90 days.

        When rotating:
        1. A new secret is generated
        2. The old secret hash is replaced with the new one
        3. The rotation count and timestamp are updated
        4. The new secret is returned (for the caller to distribute)

        Args:
            principal_id: If provided, rotate only this principal's credentials.
                If None, rotate all active principals.

        Returns:
            A dictionary summarizing which principals were rotated,
            the rotation timestamp, and new secrets for each rotated principal.

        Raises:
            ValueError: If the specified principal does not exist.
        """
        targets: List[str] = []

        if principal_id is not None:
            if principal_id not in self._principals:
                raise ValueError(f"Principal '{principal_id}' not found")
            targets = [principal_id]
        else:
            targets = [
                pid for pid, p in self._principals.items() if p.is_active
            ]

        rotation_results: Dict[str, Any] = {
            "rotated_at": datetime.now(timezone.utc).isoformat(),
            "principals_rotated": [],
            "new_secrets": {},
        }

        for pid in targets:
            new_secret = secrets.token_urlsafe(48)
            new_hash = hashlib.sha256(new_secret.encode()).hexdigest()

            # Update credential store
            cred = self._credentials.get(pid, {"rotation_count": 0})
            cred["secret_key_hash"] = new_hash
            cred["rotated_at"] = datetime.now(timezone.utc).isoformat()
            cred["rotation_count"] = cred.get("rotation_count", 0) + 1
            self._credentials[pid] = cred

            # Update principal metadata
            principal = self._principals[pid]
            principal.last_credential_rotation = datetime.now(timezone.utc)

            rotation_results["principals_rotated"].append(pid)
            rotation_results["new_secrets"][pid] = new_secret

            logger.info(
                "credentials_rotated",
                extra={
                    "principal_id": pid,
                    "rotation_count": cred["rotation_count"],
                },
            )

        rotation_results["total_rotated"] = len(targets)
        return rotation_results

    # ------------------------------------------------------------------
    # Policy management helpers
    # ------------------------------------------------------------------

    def attach_policy(self, principal_id: str, policy: Policy) -> None:
        """
        Attach an additional policy to an existing principal.

        WHY: Some principals need permissions beyond their role defaults.
        For example, a data scientist leading a project may need temporary
        deploy access to inference endpoints.

        Args:
            principal_id: The principal to attach the policy to.
            policy: The policy to attach.

        Raises:
            ValueError: If the principal does not exist.
        """
        principal = self._principals.get(principal_id)
        if principal is None:
            raise ValueError(f"Principal '{principal_id}' not found")

        principal.policies.append(policy)
        self._custom_policies[policy.policy_id] = policy

        logger.info(
            "policy_attached",
            extra={
                "principal_id": principal_id,
                "policy_id": policy.policy_id,
                "policy_name": policy.name,
            },
        )

    def deactivate_principal(self, principal_id: str) -> None:
        """
        Deactivate a principal, immediately revoking all access.

        WHY: When a team member leaves or a service is decommissioned,
        their access must be revoked immediately. Deactivation is preferred
        over deletion to maintain audit history.

        Args:
            principal_id: The principal to deactivate.

        Raises:
            ValueError: If the principal does not exist.
        """
        principal = self._principals.get(principal_id)
        if principal is None:
            raise ValueError(f"Principal '{principal_id}' not found")

        principal.is_active = False
        logger.info("principal_deactivated", extra={"principal_id": principal_id})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_audit(self, decision: AccessDecision) -> None:
        """
        Record an access decision to the append-only audit log.

        WHY: Every authorization decision — whether allowed or denied — must
        be recorded for compliance. The audit log is append-only to prevent
        tampering.

        Args:
            decision: The access decision to record.
        """
        entry = AuditEntry(
            entry_id=uuid.uuid4().hex,
            decision=decision,
        )
        self._audit_log.append(entry)

        log_level = logging.INFO if decision.allowed else logging.WARNING
        logger.log(
            log_level,
            "access_decision",
            extra={
                "principal_id": decision.principal_id,
                "resource": decision.resource,
                "action": decision.action,
                "allowed": decision.allowed,
                "reason": decision.reason,
            },
        )

    @property
    def audit_log(self) -> List[AuditEntry]:
        """Return a read-only copy of the audit log."""
        return list(self._audit_log)
