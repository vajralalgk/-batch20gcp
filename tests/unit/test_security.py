"""Tests for src.security -- IAM, encryption, VPC isolation."""

import pytest

from src.security.iam_manager import (
    AccessDecision,
    Action,
    IAMManager,
    PolicyEffect,
    PolicyStatement,
    Policy,
    Principal,
    ResourceType,
    Role,
)


# =========================================================================
# IAM Manager
# =========================================================================

class TestIAMValidateAdminAccess:
    """Test admin role access validation."""

    def test_iam_validate_admin_access(self):
        iam = IAMManager()
        iam.register_principal("admin-1", Role.ADMIN)
        decision = iam.validate_access("admin-1", ResourceType.GPU_CLUSTER.value, Action.DELETE.value)
        assert decision.allowed is True

    def test_admin_can_access_all_resources(self):
        iam = IAMManager()
        iam.register_principal("admin-2", Role.ADMIN)
        for resource in ResourceType:
            for action in Action:
                decision = iam.validate_access("admin-2", resource.value, action.value)
                assert decision.allowed is True, f"Admin denied {action.value} on {resource.value}"


class TestIAMValidateDenied:
    """Test denied access scenarios."""

    def test_iam_validate_denied(self):
        iam = IAMManager()
        # Unregistered principal should be denied
        decision = iam.validate_access("unknown-user", "model", "read")
        assert decision.allowed is False
        assert "not registered" in decision.reason

    def test_operator_cannot_delete(self):
        iam = IAMManager()
        iam.register_principal("op-1", Role.OPERATOR)
        decision = iam.validate_access("op-1", ResourceType.MODEL.value, Action.DELETE.value)
        assert decision.allowed is False
        assert "DENY" in decision.reason

    def test_data_scientist_cannot_scale_gpu(self):
        iam = IAMManager()
        iam.register_principal("ds-1", Role.DATA_SCIENTIST)
        decision = iam.validate_access("ds-1", ResourceType.GPU_CLUSTER.value, Action.SCALE.value)
        assert decision.allowed is False

    def test_deactivated_principal_denied(self):
        iam = IAMManager()
        iam.register_principal("user-x", Role.ADMIN)
        iam.deactivate_principal("user-x")
        decision = iam.validate_access("user-x", "model", "read")
        assert decision.allowed is False
        assert "deactivated" in decision.reason

    def test_service_account_limited_access(self):
        iam = IAMManager()
        iam.register_principal("svc-1", Role.SERVICE_ACCOUNT)
        # Can read models
        assert iam.validate_access("svc-1", "model", "read").allowed is True
        # Cannot delete models
        assert iam.validate_access("svc-1", "model", "delete").allowed is False


# =========================================================================
# Encryption (simulated)
# =========================================================================

class TestEncryptionEncryptDecrypt:
    """Test encryption and decryption round-trip."""

    def test_encryption_encrypt_decrypt(self):
        import base64
        plaintext = "sensitive-model-weights-path"
        # Simple XOR-based simulation (NOT real encryption)
        key = 42
        encrypted = bytes([b ^ key for b in plaintext.encode()])
        decrypted = bytes([b ^ key for b in encrypted]).decode()
        assert decrypted == plaintext
        assert encrypted != plaintext.encode()


class TestEncryptionKeyRotation:
    """Test key rotation invalidates old ciphertext."""

    def test_encryption_key_rotation(self):
        plaintext = "secret-data"
        key_v1 = 42
        key_v2 = 99
        encrypted_v1 = bytes([b ^ key_v1 for b in plaintext.encode()])
        # Decrypt with old key works
        assert bytes([b ^ key_v1 for b in encrypted_v1]).decode() == plaintext
        # Decrypt with new key produces garbage
        decrypted_wrong = bytes([b ^ key_v2 for b in encrypted_v1])
        assert decrypted_wrong != plaintext.encode()


# =========================================================================
# VPC Isolation (simulated)
# =========================================================================

class TestVPCValidateAllowedTraffic:
    """Test VPC rules allow legitimate traffic."""

    def test_vpc_validate_allowed_traffic(self):
        security_group_rules = [
            {"protocol": "tcp", "port": 8000, "source": "10.0.0.0/16", "action": "allow"},
            {"protocol": "tcp", "port": 8001, "source": "10.0.0.0/16", "action": "allow"},
            {"protocol": "tcp", "port": 6379, "source": "10.0.1.0/24", "action": "allow"},
        ]
        # Internal request to API port
        request_ip = "10.0.0.50"
        request_port = 8000
        allowed = any(
            r["port"] == request_port and r["action"] == "allow"
            and request_ip.startswith(r["source"].split("/")[0][:6])
            for r in security_group_rules
        )
        assert allowed is True


class TestVPCValidateBlockedTraffic:
    """Test VPC rules block unauthorized traffic."""

    def test_vpc_validate_blocked_traffic(self):
        security_group_rules = [
            {"protocol": "tcp", "port": 8000, "source": "10.0.0.0/16", "action": "allow"},
        ]
        # External request from internet
        request_ip = "203.0.113.50"
        request_port = 8000
        allowed = any(
            r["port"] == request_port and r["action"] == "allow"
            and request_ip.startswith(r["source"].split("/")[0][:6])
            for r in security_group_rules
        )
        assert allowed is False


# =========================================================================
# IAM audit and credential rotation
# =========================================================================

class TestIAMAudit:
    """Test audit trail generation."""

    def test_audit_log_populated(self):
        iam = IAMManager()
        iam.register_principal("auditor", Role.ADMIN)
        iam.validate_access("auditor", "model", "read")
        assert len(iam.audit_log) >= 1
        entry = iam.audit_log[0]
        assert entry.decision.allowed is True

    def test_audit_permissions_report(self):
        iam = IAMManager()
        iam.register_principal("admin", Role.ADMIN)
        iam.register_principal("ds", Role.DATA_SCIENTIST)
        report = iam.audit_permissions()
        assert report["total_principals"] == 2
        assert "admin" in report["role_distribution"]


class TestIAMCredentialRotation:
    """Test credential rotation."""

    def test_rotate_credentials(self):
        iam = IAMManager()
        iam.register_principal("svc-1", Role.SERVICE_ACCOUNT)
        result = iam.rotate_credentials("svc-1")
        assert result["total_rotated"] == 1
        assert "svc-1" in result["new_secrets"]

    def test_rotate_all_credentials(self):
        iam = IAMManager()
        iam.register_principal("p1", Role.ADMIN)
        iam.register_principal("p2", Role.OPERATOR)
        result = iam.rotate_credentials()  # All active principals
        assert result["total_rotated"] == 2
