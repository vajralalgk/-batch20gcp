"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
VPC Network Isolation & Security Group Management
============================================================================

WHY THIS MODULE EXISTS:
    The inference platform spans multiple infrastructure tiers with vastly
    different security requirements:
    - GPU inference nodes process model weights worth millions in R&D
    - Data stores hold user personalization profiles (PII under GDPR)
    - The management plane controls production deployments
    - Monitoring collects telemetry across all tiers

    Without network isolation, a compromised monitoring agent could access
    GPU nodes, or an inference pod could directly query the management
    database. This module enforces zone-based network segmentation with
    explicit allow-list routing.

DESIGN DECISIONS:
    - Four zones matching the platform's logical architecture:
      inference (GPU nodes), data (Redis, DynamoDB), management (control plane),
      monitoring (Prometheus, Grafana)
    - Default-deny between zones; only explicitly allowed flows are permitted
    - mTLS enforced on all inter-service communication (no plaintext)
    - Security groups act as virtual firewalls at the zone boundary
    - No direct external (internet) access to the inference zone;
      all inbound traffic routes through the management zone's API gateway

SECURITY IMPLICATIONS:
    - Compromise of one zone does not grant access to another
    - mTLS prevents man-in-the-middle attacks between services
    - Network audit trail captures all policy evaluations
    - External access is only permitted through the management zone

ALTERNATIVES CONSIDERED:
    - Flat network with application-layer auth only: Insufficient for defense
      in depth; network-layer isolation is a compliance requirement
    - Service mesh (Istio/Linkerd): Better for production but adds operational
      complexity; this module provides equivalent policy semantics
    - Cloud-native security groups only (AWS SGs): Less portable and harder
      to unit test; this abstraction enables consistent policy across clouds
============================================================================
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain enumerations
# ---------------------------------------------------------------------------

class NetworkZone(str, Enum):
    """
    Logical network zones in the inference platform VPC.

    WHY: Zones group infrastructure by function and trust level. Each zone
    has different security requirements and communication patterns.

    - INFERENCE: GPU nodes running model inference. Highest value target.
    - DATA: Stateful data stores (Redis, DynamoDB, S3). Contains PII.
    - MANAGEMENT: Control plane, API gateway, deployment orchestrator.
    - MONITORING: Prometheus, Grafana, log aggregators. Read-only access.
    """
    INFERENCE = "inference"
    DATA = "data"
    MANAGEMENT = "management"
    MONITORING = "monitoring"


class Protocol(str, Enum):
    """
    Network protocols allowed between zones.

    WHY: Restricting protocols narrows the attack surface. For example,
    inference nodes only need gRPC (over HTTP/2) to communicate with
    data stores, not arbitrary TCP connections.
    """
    HTTPS = "https"
    GRPC = "grpc"
    TCP = "tcp"
    UDP = "udp"


class TrafficDirection(str, Enum):
    """Traffic flow direction for security group rules."""
    INGRESS = "ingress"
    EGRESS = "egress"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NetworkRule:
    """
    A single network access rule between a source and destination zone.

    WHY: Rules are the atomic unit of network policy. Each rule specifies
    which zone can talk to which zone, over which protocols, on which ports,
    and whether mTLS is required.

    The frozen=True makes rules immutable after creation, preventing
    accidental modification of active security policies.

    Attributes:
        rule_id: Unique identifier for this rule.
        source_zone: The zone initiating the connection.
        destination_zone: The zone receiving the connection.
        allowed_protocols: Set of permitted protocols.
        allowed_ports: Set of permitted destination ports.
        mtls_required: Whether mutual TLS is required for this flow.
        description: Human-readable description of this rule's purpose.
    """
    rule_id: str
    source_zone: NetworkZone
    destination_zone: NetworkZone
    allowed_protocols: FrozenSet[str]
    allowed_ports: FrozenSet[int]
    mtls_required: bool = True
    description: str = ""


@dataclass
class SecurityGroup:
    """
    A virtual firewall for a network zone.

    WHY: Security groups aggregate rules for a zone and track metadata
    for operational visibility (when created, when last modified, status).

    Attributes:
        group_id: Unique identifier for this security group.
        name: Human-readable name.
        zone: The network zone this group protects.
        ingress_rules: Rules governing inbound traffic.
        egress_rules: Rules governing outbound traffic.
        created_at: When this group was created (UTC).
        last_modified: When this group was last updated (UTC).
        is_active: Whether this group is currently enforced.
    """
    group_id: str
    name: str
    zone: NetworkZone
    ingress_rules: List[NetworkRule] = field(default_factory=list)
    egress_rules: List[NetworkRule] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_modified: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True


@dataclass
class NetworkPolicyDecision:
    """
    The result of evaluating a network policy for a traffic flow.

    WHY: Structured decisions enable network audit logging and debugging.
    When traffic is blocked, the reason helps operators understand why
    and adjust policies if the block is unintended.

    Attributes:
        allowed: Whether the traffic flow is permitted.
        source: The source zone.
        destination: The destination zone.
        protocol: The protocol attempted.
        port: The destination port attempted.
        reason: Human-readable explanation.
        matched_rule_id: The rule that determined the outcome, if any.
        mtls_required: Whether mTLS is required for this flow.
        evaluated_at: When this decision was made (UTC).
    """
    allowed: bool
    source: str
    destination: str
    protocol: str
    port: int
    reason: str
    matched_rule_id: Optional[str] = None
    mtls_required: bool = False
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class NetworkAuditEntry:
    """
    An immutable record of a network policy evaluation.

    Attributes:
        entry_id: Unique identifier for this audit entry.
        decision: The network policy decision that was recorded.
        timestamp: When this entry was created (UTC).
    """
    entry_id: str
    decision: NetworkPolicyDecision
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Default network topology and rules
# ---------------------------------------------------------------------------

def _build_default_rules() -> List[NetworkRule]:
    """
    Construct the default network rules for the inference platform.

    WHY: These rules encode the platform's communication architecture:

    1. Inference -> Data: GPU nodes must read feature stores (Redis) and
       model registries (DynamoDB/S3) to serve predictions.

    2. Management -> All zones: The control plane needs to deploy models
       to inference nodes, manage data stores, and configure monitoring.

    3. Monitoring -> All zones (read): Prometheus scrapes metrics from all
       zones; Grafana queries all zones for dashboards.

    4. Data -> Inference: Data stores may push invalidation signals or
       stream updates to inference nodes (e.g., feature store refresh).

    NOT ALLOWED (by omission — default deny):
    - Inference -> Management: Inference nodes cannot modify the control plane
    - Inference -> Monitoring: Inference nodes do not initiate monitoring flows
    - Data -> Management: Data stores cannot control deployments
    - External -> Inference: No direct internet access to GPU nodes
    - Monitoring -> Data (write): Monitoring is read-only

    Returns:
        List of default NetworkRule instances.
    """
    return [
        # --- Inference zone egress ---
        NetworkRule(
            rule_id="rule-inference-to-data",
            source_zone=NetworkZone.INFERENCE,
            destination_zone=NetworkZone.DATA,
            allowed_protocols=frozenset({"grpc", "https"}),
            allowed_ports=frozenset({6379, 8443, 443}),
            mtls_required=True,
            description=(
                "Inference nodes access Redis feature store (6379) and "
                "DynamoDB/S3 model registry (443/8443)"
            ),
        ),

        # --- Management zone egress (to all zones) ---
        NetworkRule(
            rule_id="rule-management-to-inference",
            source_zone=NetworkZone.MANAGEMENT,
            destination_zone=NetworkZone.INFERENCE,
            allowed_protocols=frozenset({"grpc", "https"}),
            allowed_ports=frozenset({8080, 8443, 443}),
            mtls_required=True,
            description=(
                "Control plane deploys models and manages inference endpoints"
            ),
        ),
        NetworkRule(
            rule_id="rule-management-to-data",
            source_zone=NetworkZone.MANAGEMENT,
            destination_zone=NetworkZone.DATA,
            allowed_protocols=frozenset({"grpc", "https", "tcp"}),
            allowed_ports=frozenset({6379, 8443, 443, 5432, 27017}),
            mtls_required=True,
            description=(
                "Control plane manages data stores: Redis (6379), PostgreSQL "
                "(5432), MongoDB (27017), DynamoDB (443)"
            ),
        ),
        NetworkRule(
            rule_id="rule-management-to-monitoring",
            source_zone=NetworkZone.MANAGEMENT,
            destination_zone=NetworkZone.MONITORING,
            allowed_protocols=frozenset({"https"}),
            allowed_ports=frozenset({443, 3000, 9090}),
            mtls_required=True,
            description=(
                "Control plane accesses Grafana (3000) and Prometheus (9090) "
                "for operational dashboards"
            ),
        ),

        # --- Monitoring zone egress (read-only to all) ---
        NetworkRule(
            rule_id="rule-monitoring-to-inference",
            source_zone=NetworkZone.MONITORING,
            destination_zone=NetworkZone.INFERENCE,
            allowed_protocols=frozenset({"https"}),
            allowed_ports=frozenset({9090, 9091, 8080}),
            mtls_required=True,
            description=(
                "Prometheus scrapes inference node metrics (9090/9091) and "
                "health endpoints (8080)"
            ),
        ),
        NetworkRule(
            rule_id="rule-monitoring-to-data",
            source_zone=NetworkZone.MONITORING,
            destination_zone=NetworkZone.DATA,
            allowed_protocols=frozenset({"https"}),
            allowed_ports=frozenset({9090, 9091, 8080}),
            mtls_required=True,
            description=(
                "Prometheus scrapes data store exporter metrics"
            ),
        ),
        NetworkRule(
            rule_id="rule-monitoring-to-management",
            source_zone=NetworkZone.MONITORING,
            destination_zone=NetworkZone.MANAGEMENT,
            allowed_protocols=frozenset({"https"}),
            allowed_ports=frozenset({9090, 9091, 8080}),
            mtls_required=True,
            description=(
                "Prometheus scrapes management plane metrics"
            ),
        ),

        # --- Data zone egress ---
        NetworkRule(
            rule_id="rule-data-to-inference",
            source_zone=NetworkZone.DATA,
            destination_zone=NetworkZone.INFERENCE,
            allowed_protocols=frozenset({"grpc"}),
            allowed_ports=frozenset({8443}),
            mtls_required=True,
            description=(
                "Data stores push feature invalidation signals and streaming "
                "updates to inference nodes via gRPC"
            ),
        ),
    ]


def _build_zone_metadata() -> Dict[NetworkZone, Dict[str, Any]]:
    """
    Build descriptive metadata for each network zone.

    WHY: Zone metadata provides operational context — what CIDR ranges are
    assigned, what subnets exist, what the zone's purpose is. This is used
    for topology visualization and audit reports.

    Returns:
        A mapping from NetworkZone to its metadata dictionary.
    """
    return {
        NetworkZone.INFERENCE: {
            "name": "Inference Zone",
            "description": "GPU nodes running LLM inference workloads",
            "cidr": "10.0.1.0/24",
            "subnets": ["10.0.1.0/26", "10.0.1.64/26", "10.0.1.128/26"],
            "availability_zones": ["us-west-2a", "us-west-2b", "us-west-2c"],
            "components": [
                "NVIDIA A100/H100 GPU nodes",
                "TensorRT inference servers",
                "Model loading agents",
                "Feature preprocessing workers",
            ],
            "external_access": False,
        },
        NetworkZone.DATA: {
            "name": "Data Zone",
            "description": "Stateful data stores for features and model artifacts",
            "cidr": "10.0.2.0/24",
            "subnets": ["10.0.2.0/26", "10.0.2.64/26", "10.0.2.128/26"],
            "availability_zones": ["us-west-2a", "us-west-2b", "us-west-2c"],
            "components": [
                "Redis cluster (feature store)",
                "DynamoDB (user profiles)",
                "S3 (model artifact storage)",
                "PostgreSQL (model registry metadata)",
            ],
            "external_access": False,
        },
        NetworkZone.MANAGEMENT: {
            "name": "Management Zone",
            "description": "Control plane for deployments, API gateway, and orchestration",
            "cidr": "10.0.3.0/24",
            "subnets": ["10.0.3.0/26", "10.0.3.64/26"],
            "availability_zones": ["us-west-2a", "us-west-2b"],
            "components": [
                "API gateway (external entry point)",
                "Deployment orchestrator",
                "Model registry service",
                "A/B testing controller",
                "Scaling controller",
            ],
            "external_access": True,  # Only zone with external access (via ALB)
        },
        NetworkZone.MONITORING: {
            "name": "Monitoring Zone",
            "description": "Observability stack for metrics, logs, and tracing",
            "cidr": "10.0.4.0/24",
            "subnets": ["10.0.4.0/26", "10.0.4.64/26"],
            "availability_zones": ["us-west-2a", "us-west-2b"],
            "components": [
                "Prometheus (metrics collection)",
                "Grafana (dashboards)",
                "Jaeger (distributed tracing)",
                "Fluentd/OpenTelemetry (log aggregation)",
            ],
            "external_access": False,
        },
    }


# ---------------------------------------------------------------------------
# VPC Isolator
# ---------------------------------------------------------------------------

class VPCIsolator:
    """
    Network isolation and security group management for the inference platform.

    WHY: Network segmentation is the foundation of defense in depth. Even if
    an attacker compromises an application-layer component, network isolation
    limits lateral movement. This class manages:
    - Zone-based network policies with default-deny
    - Security groups per zone with ingress/egress rules
    - mTLS enforcement between all inter-zone communication
    - Network topology visibility for operations

    Communication rules:
    - Inference can access Data (read feature stores, model artifacts)
    - Management can access all zones (deploy, configure, monitor)
    - Monitoring can read from all zones (scrape metrics)
    - Data can push updates to Inference (feature invalidation)
    - No direct external access to Inference or Data zones
    - All inter-zone traffic requires mTLS

    Usage:
        isolator = VPCIsolator()
        decision = isolator.validate_network_policy("inference", "data", "grpc", 6379)
        if decision.allowed:
            # proceed with connection
            ...
    """

    def __init__(self) -> None:
        """
        Initialize the VPC isolator with default rules and security groups.

        WHY: The isolator starts with the platform's baseline network policy.
        Rules define allowed traffic flows; security groups aggregate rules
        per zone. The audit log tracks all policy evaluations.
        """
        self._rules: List[NetworkRule] = _build_default_rules()
        self._zone_metadata: Dict[NetworkZone, Dict[str, Any]] = _build_zone_metadata()
        self._security_groups: Dict[str, SecurityGroup] = {}
        self._audit_log: List[NetworkAuditEntry] = []
        self._mtls_certificates: Dict[str, Dict[str, Any]] = {}

        # Build security groups from rules
        self._initialize_security_groups()
        # Initialize mTLS certificate tracking
        self._initialize_mtls_tracking()

        logger.info(
            "vpc_isolator_initialized",
            extra={
                "zones": len(NetworkZone),
                "rules": len(self._rules),
                "security_groups": len(self._security_groups),
            },
        )

    # ------------------------------------------------------------------
    # Network policy validation
    # ------------------------------------------------------------------

    def validate_network_policy(
        self,
        source: str,
        destination: str,
        protocol: str = "https",
        port: int = 443,
    ) -> NetworkPolicyDecision:
        """
        Evaluate whether a network flow is permitted by the current policy.

        WHY: Every inter-zone connection attempt must be validated against
        the network policy before it is allowed to proceed. This method is
        the network-layer equivalent of IAMManager.validate_access().

        Evaluation order:
        1. Validate that source and destination are known zones
        2. Reject same-zone traffic (handled by intra-zone policies, not here)
        3. Search for a matching rule that allows this specific flow
        4. If no rule matches, default deny

        Args:
            source: The source network zone name (e.g., 'inference').
            destination: The destination network zone name (e.g., 'data').
            protocol: The network protocol (e.g., 'grpc', 'https').
            port: The destination port number.

        Returns:
            A NetworkPolicyDecision with the authorization result.
        """
        # Step 1: Validate zones
        try:
            source_zone = NetworkZone(source)
        except ValueError:
            decision = NetworkPolicyDecision(
                allowed=False,
                source=source,
                destination=destination,
                protocol=protocol,
                port=port,
                reason=f"Unknown source zone: '{source}'",
            )
            self._record_audit(decision)
            return decision

        try:
            dest_zone = NetworkZone(destination)
        except ValueError:
            decision = NetworkPolicyDecision(
                allowed=False,
                source=source,
                destination=destination,
                protocol=protocol,
                port=port,
                reason=f"Unknown destination zone: '{destination}'",
            )
            self._record_audit(decision)
            return decision

        # Step 2: Same-zone traffic is allowed (intra-zone)
        if source_zone == dest_zone:
            decision = NetworkPolicyDecision(
                allowed=True,
                source=source,
                destination=destination,
                protocol=protocol,
                port=port,
                reason="Intra-zone traffic is permitted",
                mtls_required=True,
            )
            self._record_audit(decision)
            return decision

        # Step 3: Search for a matching rule
        for rule in self._rules:
            if (
                rule.source_zone == source_zone
                and rule.destination_zone == dest_zone
                and protocol in rule.allowed_protocols
                and port in rule.allowed_ports
            ):
                decision = NetworkPolicyDecision(
                    allowed=True,
                    source=source,
                    destination=destination,
                    protocol=protocol,
                    port=port,
                    reason=f"Allowed by rule '{rule.rule_id}': {rule.description}",
                    matched_rule_id=rule.rule_id,
                    mtls_required=rule.mtls_required,
                )
                self._record_audit(decision)
                return decision

        # Step 4: Default deny
        decision = NetworkPolicyDecision(
            allowed=False,
            source=source,
            destination=destination,
            protocol=protocol,
            port=port,
            reason=(
                f"No rule permits {protocol}:{port} from "
                f"'{source}' to '{destination}' (default deny)"
            ),
        )
        self._record_audit(decision)
        return decision

    # ------------------------------------------------------------------
    # Security group management
    # ------------------------------------------------------------------

    def get_security_groups(self) -> Dict[str, Any]:
        """
        Retrieve all security groups and their rules.

        WHY: Operators need visibility into the current network policy state
        to troubleshoot connectivity issues and verify security posture.

        Returns:
            A dictionary containing all security groups with their ingress
            and egress rules, organized by zone.
        """
        result: Dict[str, Any] = {
            "total_groups": len(self._security_groups),
            "groups": {},
        }

        for group_id, group in self._security_groups.items():
            result["groups"][group_id] = {
                "group_id": group.group_id,
                "name": group.name,
                "zone": group.zone.value,
                "is_active": group.is_active,
                "created_at": group.created_at.isoformat(),
                "last_modified": group.last_modified.isoformat(),
                "ingress_rules": [
                    self._serialize_rule(r) for r in group.ingress_rules
                ],
                "egress_rules": [
                    self._serialize_rule(r) for r in group.egress_rules
                ],
                "total_ingress_rules": len(group.ingress_rules),
                "total_egress_rules": len(group.egress_rules),
            }

        return result

    # ------------------------------------------------------------------
    # Isolation verification
    # ------------------------------------------------------------------

    def check_isolation(self) -> Dict[str, Any]:
        """
        Verify that network isolation constraints are properly enforced.

        WHY: Drift detection is critical for security. Over time, rules may
        be added that violate the intended isolation boundaries. This method
        checks all invariants:
        1. No direct external access to inference or data zones
        2. mTLS required on all inter-zone flows
        3. Monitoring zone has no write access to other zones
        4. Inference zone cannot reach management zone
        5. All security groups are active

        Returns:
            A dictionary containing the isolation check results, including
            overall status, per-check results, and any violations found.
        """
        violations: List[str] = []
        checks: Dict[str, Dict[str, Any]] = {}

        # Check 1: No external access to inference or data zones
        check_name = "no_external_access_to_sensitive_zones"
        inference_meta = self._zone_metadata[NetworkZone.INFERENCE]
        data_meta = self._zone_metadata[NetworkZone.DATA]

        if inference_meta.get("external_access", False):
            violations.append("CRITICAL: Inference zone has external access enabled")
            checks[check_name] = {"status": "FAIL", "detail": "Inference zone exposed"}
        elif data_meta.get("external_access", False):
            violations.append("CRITICAL: Data zone has external access enabled")
            checks[check_name] = {"status": "FAIL", "detail": "Data zone exposed"}
        else:
            checks[check_name] = {"status": "PASS", "detail": "Sensitive zones isolated"}

        # Check 2: mTLS required on all inter-zone rules
        check_name = "mtls_enforced_everywhere"
        non_mtls_rules = [r for r in self._rules if not r.mtls_required]
        if non_mtls_rules:
            rule_ids = [r.rule_id for r in non_mtls_rules]
            violations.append(
                f"Rules without mTLS enforcement: {', '.join(rule_ids)}"
            )
            checks[check_name] = {
                "status": "FAIL",
                "detail": f"{len(non_mtls_rules)} rules lack mTLS",
                "affected_rules": rule_ids,
            }
        else:
            checks[check_name] = {
                "status": "PASS",
                "detail": "All inter-zone rules require mTLS",
            }

        # Check 3: Monitoring zone is read-only (no write protocols/ports to data)
        check_name = "monitoring_read_only"
        monitoring_write_violations = []
        for rule in self._rules:
            if rule.source_zone == NetworkZone.MONITORING:
                # Monitoring should only use HTTPS for scraping, not gRPC or TCP
                # which could imply write operations
                write_protocols = rule.allowed_protocols - frozenset({"https"})
                if write_protocols:
                    monitoring_write_violations.append(
                        f"{rule.rule_id}: uses {write_protocols}"
                    )
        if monitoring_write_violations:
            violations.extend(monitoring_write_violations)
            checks[check_name] = {
                "status": "FAIL",
                "detail": f"{len(monitoring_write_violations)} monitoring rules "
                          f"have non-read protocols",
            }
        else:
            checks[check_name] = {
                "status": "PASS",
                "detail": "Monitoring zone restricted to read-only protocols",
            }

        # Check 4: No inference -> management path
        check_name = "no_inference_to_management"
        inference_to_mgmt = [
            r for r in self._rules
            if r.source_zone == NetworkZone.INFERENCE
            and r.destination_zone == NetworkZone.MANAGEMENT
        ]
        if inference_to_mgmt:
            rule_ids = [r.rule_id for r in inference_to_mgmt]
            violations.append(
                f"Inference zone can reach management zone via: "
                f"{', '.join(rule_ids)}"
            )
            checks[check_name] = {
                "status": "FAIL",
                "detail": "Inference to management path exists",
                "affected_rules": rule_ids,
            }
        else:
            checks[check_name] = {
                "status": "PASS",
                "detail": "Inference zone cannot reach management zone",
            }

        # Check 5: All security groups active
        check_name = "all_security_groups_active"
        inactive_groups = [
            sg.group_id
            for sg in self._security_groups.values()
            if not sg.is_active
        ]
        if inactive_groups:
            violations.append(
                f"Inactive security groups: {', '.join(inactive_groups)}"
            )
            checks[check_name] = {
                "status": "FAIL",
                "detail": f"{len(inactive_groups)} inactive security groups",
                "affected_groups": inactive_groups,
            }
        else:
            checks[check_name] = {
                "status": "PASS",
                "detail": "All security groups are active",
            }

        # Check 6: mTLS certificates validity
        check_name = "mtls_certificates_valid"
        expired_certs = [
            zone
            for zone, cert_info in self._mtls_certificates.items()
            if cert_info.get("status") != "valid"
        ]
        if expired_certs:
            violations.append(
                f"Invalid mTLS certificates for zones: {', '.join(expired_certs)}"
            )
            checks[check_name] = {
                "status": "FAIL",
                "detail": f"{len(expired_certs)} zones with invalid certificates",
            }
        else:
            checks[check_name] = {
                "status": "PASS",
                "detail": "All mTLS certificates are valid",
            }

        # Determine overall status
        overall_status = "ISOLATED" if not violations else "VIOLATIONS_DETECTED"

        result = {
            "overall_status": overall_status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "total_checks": len(checks),
            "passed": sum(1 for c in checks.values() if c["status"] == "PASS"),
            "failed": sum(1 for c in checks.values() if c["status"] == "FAIL"),
            "checks": checks,
            "violations": violations,
        }

        log_level = logging.INFO if not violations else logging.WARNING
        logger.log(
            log_level,
            "isolation_check_completed",
            extra={
                "overall_status": overall_status,
                "violations_count": len(violations),
            },
        )

        return result

    # ------------------------------------------------------------------
    # Network topology
    # ------------------------------------------------------------------

    def get_network_topology(self) -> Dict[str, Any]:
        """
        Return the full network topology including zones, rules, and metadata.

        WHY: Network topology visualization is essential for:
        1. Architecture documentation
        2. Incident response (understanding blast radius)
        3. Compliance audits (demonstrating segmentation)
        4. Capacity planning (understanding data flow patterns)

        Returns:
            A comprehensive dictionary describing the entire network topology:
            zones with CIDR ranges and components, all rules with their
            descriptions, an adjacency matrix showing allowed flows, and
            mTLS certificate status.
        """
        # Build the zone adjacency matrix
        adjacency: Dict[str, Dict[str, List[str]]] = {}
        for zone in NetworkZone:
            adjacency[zone.value] = {}
            for other_zone in NetworkZone:
                if zone == other_zone:
                    adjacency[zone.value][other_zone.value] = ["intra-zone (allowed)"]
                else:
                    matching_rules = [
                        f"{r.rule_id} ({', '.join(sorted(r.allowed_protocols))})"
                        for r in self._rules
                        if r.source_zone == zone and r.destination_zone == other_zone
                    ]
                    adjacency[zone.value][other_zone.value] = (
                        matching_rules if matching_rules else ["BLOCKED (default deny)"]
                    )

        return {
            "topology_generated_at": datetime.now(timezone.utc).isoformat(),
            "vpc_cidr": "10.0.0.0/16",
            "zones": {
                zone.value: self._zone_metadata[zone]
                for zone in NetworkZone
            },
            "rules": [self._serialize_rule(r) for r in self._rules],
            "total_rules": len(self._rules),
            "adjacency_matrix": adjacency,
            "mtls_status": {
                zone: info.get("status", "unknown")
                for zone, info in self._mtls_certificates.items()
            },
            "security_groups_count": len(self._security_groups),
        }

    # ------------------------------------------------------------------
    # Network access audit
    # ------------------------------------------------------------------

    def audit_network_access(self) -> Dict[str, Any]:
        """
        Generate a comprehensive network access audit report.

        WHY: Network audit reports are required for:
        1. SOC2 compliance: Demonstrate that network segmentation is enforced
        2. Incident investigation: Trace what flows were allowed/denied
        3. Policy optimization: Identify unused rules that can be tightened

        Returns:
            A dictionary containing the audit report with statistics on
            allowed/denied flows, per-zone breakdowns, per-rule hit counts,
            and the most recent decisions.
        """
        total_decisions = len(self._audit_log)
        allowed_count = sum(
            1 for entry in self._audit_log if entry.decision.allowed
        )
        denied_count = total_decisions - allowed_count

        # Per-zone statistics
        zone_stats: Dict[str, Dict[str, int]] = {}
        for zone in NetworkZone:
            as_source_allowed = sum(
                1 for e in self._audit_log
                if e.decision.source == zone.value and e.decision.allowed
            )
            as_source_denied = sum(
                1 for e in self._audit_log
                if e.decision.source == zone.value and not e.decision.allowed
            )
            as_dest_allowed = sum(
                1 for e in self._audit_log
                if e.decision.destination == zone.value and e.decision.allowed
            )
            as_dest_denied = sum(
                1 for e in self._audit_log
                if e.decision.destination == zone.value and not e.decision.allowed
            )
            zone_stats[zone.value] = {
                "outbound_allowed": as_source_allowed,
                "outbound_denied": as_source_denied,
                "inbound_allowed": as_dest_allowed,
                "inbound_denied": as_dest_denied,
            }

        # Per-rule hit count
        rule_hits: Dict[str, int] = {}
        for entry in self._audit_log:
            rid = entry.decision.matched_rule_id
            if rid:
                rule_hits[rid] = rule_hits.get(rid, 0) + 1

        # Unused rules (defined but never matched)
        all_rule_ids = {r.rule_id for r in self._rules}
        used_rule_ids = set(rule_hits.keys())
        unused_rules = sorted(all_rule_ids - used_rule_ids)

        # Recent decisions (last 50)
        recent_decisions = [
            {
                "source": e.decision.source,
                "destination": e.decision.destination,
                "protocol": e.decision.protocol,
                "port": e.decision.port,
                "allowed": e.decision.allowed,
                "reason": e.decision.reason,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in self._audit_log[-50:]
        ]

        report = {
            "audit_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_evaluations": total_decisions,
            "allowed": allowed_count,
            "denied": denied_count,
            "denial_rate": (
                round(denied_count / total_decisions * 100, 2)
                if total_decisions > 0
                else 0.0
            ),
            "zone_statistics": zone_stats,
            "rule_hit_counts": rule_hits,
            "unused_rules": unused_rules,
            "unused_rules_count": len(unused_rules),
            "recent_decisions": recent_decisions,
            "mtls_enforcement": {
                zone: info
                for zone, info in self._mtls_certificates.items()
            },
        }

        logger.info(
            "network_audit_completed",
            extra={
                "total_evaluations": total_decisions,
                "denied": denied_count,
                "unused_rules": len(unused_rules),
            },
        )

        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _initialize_security_groups(self) -> None:
        """
        Build security groups from the default rules.

        WHY: Security groups aggregate rules per zone, making it easy to
        query what traffic a specific zone allows in and out.
        """
        for zone in NetworkZone:
            group_id = f"sg-{zone.value}-{uuid.uuid4().hex[:8]}"
            group = SecurityGroup(
                group_id=group_id,
                name=f"{zone.value}-security-group",
                zone=zone,
            )

            # Assign ingress rules (destination == this zone)
            group.ingress_rules = [
                r for r in self._rules if r.destination_zone == zone
            ]
            # Assign egress rules (source == this zone)
            group.egress_rules = [
                r for r in self._rules if r.source_zone == zone
            ]

            self._security_groups[group_id] = group

    def _initialize_mtls_tracking(self) -> None:
        """
        Initialize mTLS certificate tracking for each zone.

        WHY: mTLS ensures mutual authentication between services. Tracking
        certificate status per zone enables rotation monitoring and
        expiration alerting.
        """
        now = datetime.now(timezone.utc)
        for zone in NetworkZone:
            self._mtls_certificates[zone.value] = {
                "certificate_id": f"cert-{zone.value}-{uuid.uuid4().hex[:8]}",
                "zone": zone.value,
                "issued_at": now.isoformat(),
                "expires_at": now.replace(year=now.year + 1).isoformat(),
                "issuer": "netflix-internal-ca",
                "status": "valid",
                "key_algorithm": "ECDSA-P256",
                "serial_number": uuid.uuid4().hex,
            }

    def _record_audit(self, decision: NetworkPolicyDecision) -> None:
        """
        Record a network policy decision to the audit log.

        WHY: Every network policy evaluation must be recorded for compliance
        and incident investigation. The audit log is append-only.

        Args:
            decision: The network policy decision to record.
        """
        entry = NetworkAuditEntry(
            entry_id=uuid.uuid4().hex,
            decision=decision,
        )
        self._audit_log.append(entry)

        log_level = logging.DEBUG if decision.allowed else logging.WARNING
        logger.log(
            log_level,
            "network_policy_decision",
            extra={
                "source": decision.source,
                "destination": decision.destination,
                "protocol": decision.protocol,
                "port": decision.port,
                "allowed": decision.allowed,
                "reason": decision.reason,
            },
        )

    @staticmethod
    def _serialize_rule(rule: NetworkRule) -> Dict[str, Any]:
        """Convert a NetworkRule to a JSON-serializable dictionary."""
        return {
            "rule_id": rule.rule_id,
            "source_zone": rule.source_zone.value,
            "destination_zone": rule.destination_zone.value,
            "allowed_protocols": sorted(rule.allowed_protocols),
            "allowed_ports": sorted(rule.allowed_ports),
            "mtls_required": rule.mtls_required,
            "description": rule.description,
        }

    @property
    def audit_log(self) -> List[NetworkAuditEntry]:
        """Return a read-only copy of the network audit log."""
        return list(self._audit_log)
