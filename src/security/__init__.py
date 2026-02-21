"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
Security Module
============================================================================

WHY THIS MODULE EXISTS:
    The security module provides comprehensive security infrastructure for the
    Netflix LLM inference platform. It enforces defense-in-depth across three
    critical domains:

    1. Identity & Access Management (IAMManager)
       - Role-based access control with deny-override policy evaluation
       - Service role provisioning for automated workloads
       - Credential rotation and permission auditing

    2. Encryption (EncryptionManager)
       - AES-256-GCM encryption for model artifacts at rest
       - Envelope encryption with KMS-managed data encryption keys
       - Automatic key rotation with zero-downtime transitions

    3. Network Isolation (VPCIsolator)
       - Zone-based network segmentation (inference, data, management, monitoring)
       - mTLS enforcement between all internal services
       - Security group management and network policy validation

DESIGN DECISIONS:
    - Defense-in-depth: Each layer operates independently so a failure in one
      does not compromise the others
    - Deny-override: IAM policies default to deny; explicit deny always wins
      over explicit allow, following AWS IAM semantics
    - Envelope encryption: Model artifacts are encrypted with per-artifact
      data keys, which are themselves encrypted with a KMS master key
    - Zone isolation: GPU inference nodes are never directly exposed to
      external traffic; all access goes through the management zone

SECURITY IMPLICATIONS:
    - All cryptographic keys are managed through KMS (mocked for development)
    - Network policies enforce least-privilege communication between zones
    - Audit logs capture every access decision for compliance reporting
============================================================================
"""

from src.security.iam_manager import IAMManager
from src.security.encryption import EncryptionManager
from src.security.vpc_isolator import VPCIsolator

__all__ = [
    "IAMManager",
    "EncryptionManager",
    "VPCIsolator",
]
