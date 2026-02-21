"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Unit Tests for Core Utilities (helpers.py)
Author: Gopi Krishna Vajrala
============================================================================

Tests cover:
    - generate_id: prefix formatting, uniqueness, length
    - now_utc: timezone awareness, UTC correctness
    - sanitize_input: control char removal, null byte removal,
      whitespace stripping, length truncation, non-string handling
    - build_aws_arn: ARN format, default region, all components
    - mask_sensitive_string: masking logic, edge cases,
      short strings, empty strings
============================================================================
"""

import re
from datetime import datetime, timezone

import pytest

from src.core.utils.helpers import (
    generate_id,
    now_utc,
    sanitize_input,
    build_aws_arn,
    mask_sensitive_string,
)


class TestGenerateId:
    """Tests for the generate_id function."""

    def test_default_prefix(self):
        """Default prefix is 'ectp'."""
        result = generate_id()
        assert result.startswith("ectp-")

    def test_custom_prefix(self):
        """Custom prefix is used in generated ID."""
        result = generate_id(prefix="mig")
        assert result.startswith("mig-")

    def test_format_has_prefix_and_unique_part(self):
        """ID format is '{prefix}-{12_hex_chars}'."""
        result = generate_id(prefix="test")
        parts = result.split("-", 1)
        assert len(parts) == 2
        assert parts[0] == "test"
        assert len(parts[1]) == 12

    def test_unique_part_is_hex(self):
        """Unique part contains only hexadecimal characters."""
        result = generate_id()
        unique_part = result.split("-", 1)[1]
        assert re.match(r'^[0-9a-f]{12}$', unique_part)

    def test_ids_are_unique(self):
        """Two calls produce different IDs."""
        id1 = generate_id()
        id2 = generate_id()
        assert id1 != id2

    def test_many_ids_are_unique(self):
        """1000 IDs are all unique (collision resistance)."""
        ids = {generate_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_empty_prefix(self):
        """Empty prefix produces '-{unique_part}' format."""
        result = generate_id(prefix="")
        assert result.startswith("-")


class TestNowUtc:
    """Tests for the now_utc function."""

    def test_returns_datetime(self):
        """Returns a datetime object."""
        result = now_utc()
        assert isinstance(result, datetime)

    def test_timezone_is_utc(self):
        """Returned datetime has UTC timezone."""
        result = now_utc()
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc

    def test_is_recent(self):
        """Returned time is within a few seconds of now."""
        before = datetime.now(timezone.utc)
        result = now_utc()
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_successive_calls_increase(self):
        """Two successive calls return non-decreasing timestamps."""
        t1 = now_utc()
        t2 = now_utc()
        assert t2 >= t1


class TestSanitizeInput:
    """Tests for the sanitize_input function."""

    def test_normal_input_unchanged(self):
        """Normal text passes through unchanged."""
        result = sanitize_input("Hello, World!")
        assert result == "Hello, World!"

    def test_strips_whitespace(self):
        """Leading and trailing whitespace is removed."""
        result = sanitize_input("  hello  ")
        assert result == "hello"

    def test_removes_null_bytes(self):
        """Null bytes are removed."""
        result = sanitize_input("hello\x00world")
        assert result == "helloworld"

    def test_removes_control_characters(self):
        """Control characters (except tab, newline, CR) are removed."""
        result = sanitize_input("hello\x01\x02\x03world")
        assert result == "helloworld"

    def test_preserves_tab(self):
        """Tab character is preserved."""
        result = sanitize_input("hello\tworld")
        assert "\t" in result

    def test_preserves_newline(self):
        """Newline character is preserved."""
        result = sanitize_input("hello\nworld")
        assert "\n" in result

    def test_preserves_carriage_return(self):
        """Carriage return is preserved."""
        result = sanitize_input("hello\rworld")
        assert "\r" in result

    def test_truncates_to_max_length(self):
        """Input is truncated to max_length."""
        long_input = "a" * 2000
        result = sanitize_input(long_input, max_length=1000)
        assert len(result) == 1000

    def test_custom_max_length(self):
        """Custom max_length is respected."""
        result = sanitize_input("hello world", max_length=5)
        assert len(result) == 5
        assert result == "hello"

    def test_default_max_length_is_1000(self):
        """Default max_length is 1000."""
        input_str = "x" * 1500
        result = sanitize_input(input_str)
        assert len(result) == 1000

    def test_short_input_not_truncated(self):
        """Input shorter than max_length is not truncated."""
        result = sanitize_input("short", max_length=1000)
        assert result == "short"

    def test_non_string_input_converted(self):
        """Non-string input is converted to string."""
        result = sanitize_input(12345)
        assert result == "12345"

    def test_empty_string(self):
        """Empty string returns empty string."""
        result = sanitize_input("")
        assert result == ""


class TestBuildAwsArn:
    """Tests for the build_aws_arn function."""

    def test_basic_arn_format(self):
        """ARN follows standard AWS format."""
        result = build_aws_arn(
            service="ec2",
            resource_type="instance",
            resource_id="i-1234567890",
            region="us-east-1",
            account_id="123456789012",
        )
        assert result == "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890"

    def test_default_region(self):
        """Default region is us-east-1."""
        result = build_aws_arn(
            service="s3",
            resource_type="bucket",
            resource_id="my-bucket",
            account_id="123456789012",
        )
        assert ":us-east-1:" in result

    def test_custom_region(self):
        """Custom region is included in ARN."""
        result = build_aws_arn(
            service="rds",
            resource_type="db",
            resource_id="mydb",
            region="us-west-2",
            account_id="123456789012",
        )
        assert ":us-west-2:" in result

    def test_arn_starts_with_arn_aws(self):
        """ARN starts with 'arn:aws:'."""
        result = build_aws_arn("s3", "bucket", "test", "us-east-1", "123")
        assert result.startswith("arn:aws:")

    def test_service_in_arn(self):
        """Service name appears in the ARN."""
        result = build_aws_arn("rds", "db", "mydb")
        assert ":rds:" in result

    def test_resource_type_and_id_in_arn(self):
        """Resource type and ID appear in the ARN."""
        result = build_aws_arn("ecs", "service", "my-service")
        assert "service/my-service" in result

    def test_empty_account_id(self):
        """Empty account_id produces ARN with empty account field."""
        result = build_aws_arn("s3", "bucket", "test")
        assert "::bucket" in result


class TestMaskSensitiveString:
    """Tests for the mask_sensitive_string function."""

    def test_basic_masking(self):
        """String is masked with asterisks except last N chars."""
        result = mask_sensitive_string("sk-1234567890abcdef")
        assert result.endswith("cdef")
        assert result.startswith("*")
        assert len(result) == len("sk-1234567890abcdef")

    def test_default_visible_chars_is_4(self):
        """Default visible characters is 4."""
        result = mask_sensitive_string("1234567890")
        visible = result.replace("*", "")
        assert visible == "7890"

    def test_custom_visible_chars(self):
        """Custom visible_chars count is respected."""
        result = mask_sensitive_string("1234567890", visible_chars=2)
        visible = result.replace("*", "")
        assert visible == "90"

    def test_asterisk_count(self):
        """Correct number of asterisks in masked output."""
        result = mask_sensitive_string("1234567890", visible_chars=4)
        assert result.count("*") == 6

    def test_short_string_fully_masked(self):
        """String shorter than visible_chars is fully masked."""
        result = mask_sensitive_string("abc")
        assert result == "***"

    def test_string_equal_to_visible_chars(self):
        """String equal to visible_chars length is fully masked."""
        result = mask_sensitive_string("abcd", visible_chars=4)
        assert result == "****"

    def test_empty_string(self):
        """Empty string returns empty string."""
        result = mask_sensitive_string("")
        assert result == ""

    def test_single_character(self):
        """Single character string is fully masked."""
        result = mask_sensitive_string("x")
        assert result == "*"

    def test_preserves_total_length(self):
        """Masked string preserves the original length."""
        original = "my-super-secret-api-key-12345"
        result = mask_sensitive_string(original)
        assert len(result) == len(original)
