"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
AWS Client Wrapper
Author: Gopi Krishna Vajrala
============================================================================

Unified AWS client wrapper providing access to S3, DynamoDB, and STS
services. Supports a mock mode for local development and testing without
requiring AWS credentials.

Usage:
    # Production mode (uses real AWS credentials)
    client = AWSClient(region="us-east-1")

    # Mock mode (returns simulated responses)
    client = AWSClient(region="us-east-1", mock_mode=True)

    # S3 operations
    await client.s3_get_object("my-bucket", "path/to/key")
    await client.s3_put_object("my-bucket", "path/to/key", b"data")
    await client.s3_list_objects("my-bucket", prefix="models/")

    # DynamoDB operations
    await client.dynamodb_get_item("my-table", {"pk": "user-123"})
    await client.dynamodb_put_item("my-table", {"pk": "user-123", "data": "..."})
    await client.dynamodb_query("my-table", "pk = :pk", {":pk": "user-123"})

    # STS operations
    identity = await client.sts_get_caller_identity()
============================================================================
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("netflix_llm_platform.aws")


class AWSClientError(Exception):
    """Base exception for AWS client errors."""

    def __init__(self, service: str, operation: str, message: str):
        self.service = service
        self.operation = operation
        self.message = message
        super().__init__(f"[{service}:{operation}] {message}")


class AWSClient:
    """
    Unified AWS client wrapper for S3, DynamoDB, and STS.

    Provides both real AWS SDK access (via boto3) and a mock mode
    that returns realistic simulated responses for local development
    and testing.

    Attributes:
        region: AWS region (e.g., "us-east-1").
        mock_mode: If True, returns simulated responses without
                   making actual AWS API calls.
        profile: Optional AWS CLI profile name for credential lookup.
    """

    def __init__(
        self,
        region: str = "us-east-1",
        mock_mode: bool = False,
        profile: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ):
        """
        Initialize the AWS client.

        Args:
            region: AWS region for API calls.
            mock_mode: Enable mock mode for local development.
            profile: AWS CLI profile name (optional).
            endpoint_url: Custom endpoint URL for LocalStack or similar
                          (optional).
        """
        self.region = region
        self.mock_mode = mock_mode
        self.profile = profile
        self.endpoint_url = endpoint_url

        self._s3_client = None
        self._dynamodb_client = None
        self._dynamodb_resource = None
        self._sts_client = None
        self._session = None

        # Mock data stores
        self._mock_s3_store: Dict[str, Dict[str, bytes]] = {}
        self._mock_dynamodb_store: Dict[str, List[Dict[str, Any]]] = {}

        if mock_mode:
            logger.info(
                "AWS client initialized in MOCK mode (region=%s)", region
            )
        else:
            logger.info(
                "AWS client initialized (region=%s, profile=%s)",
                region,
                profile or "default",
            )

    # ------------------------------------------------------------------
    # Session / Client Initialization
    # ------------------------------------------------------------------
    def _get_session(self):
        """Get or create a boto3 session."""
        if self._session is None:
            try:
                import boto3

                kwargs: Dict[str, Any] = {"region_name": self.region}
                if self.profile:
                    kwargs["profile_name"] = self.profile
                self._session = boto3.Session(**kwargs)
            except ImportError:
                raise AWSClientError(
                    "core",
                    "init",
                    "boto3 is not installed. Install it with: pip install boto3",
                )
        return self._session

    def _get_s3_client(self):
        """Get or create an S3 client."""
        if self._s3_client is None:
            session = self._get_session()
            kwargs: Dict[str, Any] = {}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self._s3_client = session.client("s3", **kwargs)
        return self._s3_client

    def _get_dynamodb_client(self):
        """Get or create a DynamoDB client."""
        if self._dynamodb_client is None:
            session = self._get_session()
            kwargs: Dict[str, Any] = {}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self._dynamodb_client = session.client("dynamodb", **kwargs)
        return self._dynamodb_client

    def _get_dynamodb_resource(self):
        """Get or create a DynamoDB resource (high-level API)."""
        if self._dynamodb_resource is None:
            session = self._get_session()
            kwargs: Dict[str, Any] = {}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self._dynamodb_resource = session.resource("dynamodb", **kwargs)
        return self._dynamodb_resource

    def _get_sts_client(self):
        """Get or create an STS client."""
        if self._sts_client is None:
            session = self._get_session()
            kwargs: Dict[str, Any] = {}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self._sts_client = session.client("sts", **kwargs)
        return self._sts_client

    # ------------------------------------------------------------------
    # S3 Operations
    # ------------------------------------------------------------------
    async def s3_get_object(
        self, bucket: str, key: str
    ) -> Dict[str, Any]:
        """
        Retrieve an object from S3.

        Args:
            bucket: S3 bucket name.
            key: Object key (path).

        Returns:
            Dict with 'body' (bytes), 'content_type', 'content_length',
            'last_modified', and 'metadata'.
        """
        logger.debug("s3_get_object bucket=%s key=%s", bucket, key)

        if self.mock_mode:
            store = self._mock_s3_store.get(bucket, {})
            if key in store:
                data = store[key]
                return {
                    "body": data,
                    "content_type": "application/octet-stream",
                    "content_length": len(data),
                    "last_modified": datetime.now(timezone.utc).isoformat(),
                    "metadata": {},
                    "etag": f'"{uuid.uuid4().hex[:32]}"',
                }
            else:
                mock_body = json.dumps({
                    "mock": True,
                    "bucket": bucket,
                    "key": key,
                }).encode()
                return {
                    "body": mock_body,
                    "content_type": "application/json",
                    "content_length": len(mock_body),
                    "last_modified": datetime.now(timezone.utc).isoformat(),
                    "metadata": {"mock": "true"},
                    "etag": f'"{uuid.uuid4().hex[:32]}"',
                }

        try:
            client = self._get_s3_client()
            response = client.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read()
            return {
                "body": body,
                "content_type": response.get("ContentType", "application/octet-stream"),
                "content_length": response.get("ContentLength", len(body)),
                "last_modified": (
                    response["LastModified"].isoformat()
                    if response.get("LastModified")
                    else None
                ),
                "metadata": response.get("Metadata", {}),
                "etag": response.get("ETag", ""),
            }
        except Exception as e:
            raise AWSClientError("s3", "get_object", str(e))

    async def s3_put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Upload an object to S3.

        Args:
            bucket: S3 bucket name.
            key: Object key (path).
            body: Object data as bytes.
            content_type: MIME type of the object.
            metadata: Optional metadata dict.

        Returns:
            Dict with 'etag', 'version_id', and 'request_id'.
        """
        logger.debug(
            "s3_put_object bucket=%s key=%s size=%d",
            bucket,
            key,
            len(body),
        )

        if self.mock_mode:
            if bucket not in self._mock_s3_store:
                self._mock_s3_store[bucket] = {}
            self._mock_s3_store[bucket][key] = body
            return {
                "etag": f'"{uuid.uuid4().hex[:32]}"',
                "version_id": None,
                "request_id": str(uuid.uuid4()),
            }

        try:
            client = self._get_s3_client()
            kwargs: Dict[str, Any] = {
                "Bucket": bucket,
                "Key": key,
                "Body": body,
                "ContentType": content_type,
            }
            if metadata:
                kwargs["Metadata"] = metadata
            response = client.put_object(**kwargs)
            return {
                "etag": response.get("ETag", ""),
                "version_id": response.get("VersionId"),
                "request_id": response.get("ResponseMetadata", {}).get(
                    "RequestId", ""
                ),
            }
        except Exception as e:
            raise AWSClientError("s3", "put_object", str(e))

    async def s3_list_objects(
        self,
        bucket: str,
        prefix: str = "",
        max_keys: int = 1000,
    ) -> Dict[str, Any]:
        """
        List objects in an S3 bucket with an optional prefix filter.

        Args:
            bucket: S3 bucket name.
            prefix: Key prefix filter.
            max_keys: Maximum number of keys to return.

        Returns:
            Dict with 'objects' (list of key info) and 'is_truncated'.
        """
        logger.debug(
            "s3_list_objects bucket=%s prefix=%s max_keys=%d",
            bucket,
            prefix,
            max_keys,
        )

        if self.mock_mode:
            store = self._mock_s3_store.get(bucket, {})
            matching = [
                {
                    "key": k,
                    "size": len(v),
                    "last_modified": datetime.now(timezone.utc).isoformat(),
                    "storage_class": "STANDARD",
                }
                for k, v in store.items()
                if k.startswith(prefix)
            ]
            # Return realistic mock entries if store is empty
            if not matching:
                matching = [
                    {
                        "key": f"{prefix}model-weights-shard-{i:03d}.safetensors",
                        "size": 2_147_483_648,
                        "last_modified": "2026-02-18T14:30:00Z",
                        "storage_class": "STANDARD",
                    }
                    for i in range(4)
                ]
            return {
                "objects": matching[:max_keys],
                "is_truncated": len(matching) > max_keys,
                "key_count": min(len(matching), max_keys),
                "prefix": prefix,
            }

        try:
            client = self._get_s3_client()
            response = client.list_objects_v2(
                Bucket=bucket,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
            objects = [
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": (
                        obj["LastModified"].isoformat()
                        if obj.get("LastModified")
                        else None
                    ),
                    "storage_class": obj.get("StorageClass", "STANDARD"),
                }
                for obj in response.get("Contents", [])
            ]
            return {
                "objects": objects,
                "is_truncated": response.get("IsTruncated", False),
                "key_count": response.get("KeyCount", len(objects)),
                "prefix": prefix,
            }
        except Exception as e:
            raise AWSClientError("s3", "list_objects", str(e))

    # ------------------------------------------------------------------
    # DynamoDB Operations
    # ------------------------------------------------------------------
    async def dynamodb_get_item(
        self,
        table_name: str,
        key: Dict[str, Any],
        consistent_read: bool = False,
    ) -> Dict[str, Any]:
        """
        Retrieve an item from a DynamoDB table.

        Args:
            table_name: DynamoDB table name.
            key: Primary key dict (e.g., {"pk": "user-123"}).
            consistent_read: Use strongly consistent read.

        Returns:
            Dict with 'item' (the item data or None) and 'consumed_capacity'.
        """
        logger.debug(
            "dynamodb_get_item table=%s key=%s",
            table_name,
            json.dumps(key),
        )

        if self.mock_mode:
            table_data = self._mock_dynamodb_store.get(table_name, [])
            for item in table_data:
                if all(item.get(k) == v for k, v in key.items()):
                    return {
                        "item": item,
                        "consumed_capacity": {
                            "table_name": table_name,
                            "capacity_units": 0.5,
                        },
                    }
            # Return a realistic mock item
            return {
                "item": {
                    **key,
                    "embedding_vector": [0.0] * 10,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "version": 1,
                    "ttl": int(time.time()) + 3600,
                    "_mock": True,
                },
                "consumed_capacity": {
                    "table_name": table_name,
                    "capacity_units": 0.5,
                },
            }

        try:
            client = self._get_dynamodb_client()
            ddb_key = self._to_dynamodb_item(key)
            response = client.get_item(
                TableName=table_name,
                Key=ddb_key,
                ConsistentRead=consistent_read,
                ReturnConsumedCapacity="TOTAL",
            )
            item = response.get("Item")
            return {
                "item": self._from_dynamodb_item(item) if item else None,
                "consumed_capacity": response.get("ConsumedCapacity"),
            }
        except Exception as e:
            raise AWSClientError("dynamodb", "get_item", str(e))

    async def dynamodb_put_item(
        self,
        table_name: str,
        item: Dict[str, Any],
        condition_expression: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Write an item to a DynamoDB table.

        Args:
            table_name: DynamoDB table name.
            item: The item to write.
            condition_expression: Optional condition for conditional writes.

        Returns:
            Dict with 'success' flag and 'consumed_capacity'.
        """
        logger.debug(
            "dynamodb_put_item table=%s item_keys=%s",
            table_name,
            list(item.keys()),
        )

        if self.mock_mode:
            if table_name not in self._mock_dynamodb_store:
                self._mock_dynamodb_store[table_name] = []
            self._mock_dynamodb_store[table_name].append(item)
            return {
                "success": True,
                "consumed_capacity": {
                    "table_name": table_name,
                    "capacity_units": 1.0,
                },
            }

        try:
            client = self._get_dynamodb_client()
            ddb_item = self._to_dynamodb_item(item)
            kwargs: Dict[str, Any] = {
                "TableName": table_name,
                "Item": ddb_item,
                "ReturnConsumedCapacity": "TOTAL",
            }
            if condition_expression:
                kwargs["ConditionExpression"] = condition_expression
            response = client.put_item(**kwargs)
            return {
                "success": True,
                "consumed_capacity": response.get("ConsumedCapacity"),
            }
        except Exception as e:
            raise AWSClientError("dynamodb", "put_item", str(e))

    async def dynamodb_query(
        self,
        table_name: str,
        key_condition_expression: str,
        expression_attribute_values: Dict[str, Any],
        limit: int = 100,
        scan_index_forward: bool = True,
        index_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query a DynamoDB table using a key condition expression.

        Args:
            table_name: DynamoDB table name.
            key_condition_expression: The key condition (e.g., "pk = :pk").
            expression_attribute_values: Values for expression placeholders.
            limit: Maximum items to return.
            scan_index_forward: True for ascending, False for descending.
            index_name: Optional GSI/LSI name.

        Returns:
            Dict with 'items', 'count', 'scanned_count', and
            'consumed_capacity'.
        """
        logger.debug(
            "dynamodb_query table=%s condition=%s",
            table_name,
            key_condition_expression,
        )

        if self.mock_mode:
            mock_items = [
                {
                    "pk": f"user-{i:05d}",
                    "sk": f"feature#{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                    "embedding": [round(0.01 * i, 4)] * 10,
                    "score": round(0.5 + 0.05 * i, 4),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(min(limit, 5))
            ]
            return {
                "items": mock_items,
                "count": len(mock_items),
                "scanned_count": len(mock_items),
                "consumed_capacity": {
                    "table_name": table_name,
                    "capacity_units": 2.5,
                },
                "last_evaluated_key": None,
            }

        try:
            client = self._get_dynamodb_client()
            ddb_values = self._to_dynamodb_item(expression_attribute_values)
            kwargs: Dict[str, Any] = {
                "TableName": table_name,
                "KeyConditionExpression": key_condition_expression,
                "ExpressionAttributeValues": ddb_values,
                "Limit": limit,
                "ScanIndexForward": scan_index_forward,
                "ReturnConsumedCapacity": "TOTAL",
            }
            if index_name:
                kwargs["IndexName"] = index_name
            response = client.query(**kwargs)
            items = [
                self._from_dynamodb_item(item)
                for item in response.get("Items", [])
            ]
            return {
                "items": items,
                "count": response.get("Count", 0),
                "scanned_count": response.get("ScannedCount", 0),
                "consumed_capacity": response.get("ConsumedCapacity"),
                "last_evaluated_key": response.get("LastEvaluatedKey"),
            }
        except Exception as e:
            raise AWSClientError("dynamodb", "query", str(e))

    # ------------------------------------------------------------------
    # STS Operations
    # ------------------------------------------------------------------
    async def sts_get_caller_identity(self) -> Dict[str, Any]:
        """
        Get the IAM identity of the caller.

        Returns:
            Dict with 'account', 'arn', and 'user_id'.
        """
        logger.debug("sts_get_caller_identity")

        if self.mock_mode:
            return {
                "account": "123456789012",
                "arn": "arn:aws:iam::123456789012:role/nflx-llm-platform-prod",
                "user_id": "AROA3XFRBF23XXXXXX:nflx-llm-platform-session",
                "region": self.region,
                "mock": True,
            }

        try:
            client = self._get_sts_client()
            response = client.get_caller_identity()
            return {
                "account": response["Account"],
                "arn": response["Arn"],
                "user_id": response["UserId"],
                "region": self.region,
            }
        except Exception as e:
            raise AWSClientError("sts", "get_caller_identity", str(e))

    async def sts_assume_role(
        self,
        role_arn: str,
        session_name: str = "nflx-llm-platform",
        duration_seconds: int = 3600,
    ) -> Dict[str, Any]:
        """
        Assume an IAM role and return temporary credentials.

        Args:
            role_arn: ARN of the role to assume.
            session_name: Name for the assumed role session.
            duration_seconds: Credential validity period.

        Returns:
            Dict with 'credentials' (access_key, secret_key,
            session_token, expiration) and 'assumed_role_user'.
        """
        logger.debug(
            "sts_assume_role role_arn=%s session=%s",
            role_arn,
            session_name,
        )

        if self.mock_mode:
            return {
                "credentials": {
                    "access_key_id": "ASIAXXXXXXXXXXXXXXXX",
                    "secret_access_key": "mock-secret-key-not-real",
                    "session_token": "mock-session-token-not-real",
                    "expiration": datetime.now(timezone.utc).isoformat(),
                },
                "assumed_role_user": {
                    "arn": f"{role_arn}/{session_name}",
                    "assumed_role_id": f"AROA3XFRBF23XXXXXX:{session_name}",
                },
                "mock": True,
            }

        try:
            client = self._get_sts_client()
            response = client.assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                DurationSeconds=duration_seconds,
            )
            creds = response["Credentials"]
            return {
                "credentials": {
                    "access_key_id": creds["AccessKeyId"],
                    "secret_access_key": creds["SecretAccessKey"],
                    "session_token": creds["SessionToken"],
                    "expiration": creds["Expiration"].isoformat(),
                },
                "assumed_role_user": {
                    "arn": response["AssumedRoleUser"]["Arn"],
                    "assumed_role_id": response["AssumedRoleUser"][
                        "AssumedRoleId"
                    ],
                },
            }
        except Exception as e:
            raise AWSClientError("sts", "assume_role", str(e))

    # ------------------------------------------------------------------
    # DynamoDB Type Conversion Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _to_dynamodb_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a Python dict to DynamoDB typed attribute format."""
        result = {}
        for key, value in item.items():
            result[key] = AWSClient._to_dynamodb_value(value)
        return result

    @staticmethod
    def _to_dynamodb_value(value: Any) -> Dict[str, Any]:
        """Convert a single Python value to DynamoDB typed attribute."""
        if isinstance(value, str):
            return {"S": value}
        elif isinstance(value, bool):
            # bool check must come before int since bool is subclass of int
            return {"BOOL": value}
        elif isinstance(value, (int, float)):
            return {"N": str(value)}
        elif isinstance(value, bytes):
            return {"B": value}
        elif isinstance(value, list):
            return {
                "L": [AWSClient._to_dynamodb_value(v) for v in value]
            }
        elif isinstance(value, dict):
            return {"M": AWSClient._to_dynamodb_item(value)}
        elif value is None:
            return {"NULL": True}
        else:
            return {"S": str(value)}

    @staticmethod
    def _from_dynamodb_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a DynamoDB typed attribute dict to a plain Python dict."""
        result = {}
        for key, value in item.items():
            result[key] = AWSClient._from_dynamodb_value(value)
        return result

    @staticmethod
    def _from_dynamodb_value(value: Dict[str, Any]) -> Any:
        """Convert a single DynamoDB typed attribute to a Python value."""
        if "S" in value:
            return value["S"]
        elif "N" in value:
            num_str = value["N"]
            return int(num_str) if "." not in num_str else float(num_str)
        elif "BOOL" in value:
            return value["BOOL"]
        elif "B" in value:
            return value["B"]
        elif "NULL" in value:
            return None
        elif "L" in value:
            return [AWSClient._from_dynamodb_value(v) for v in value["L"]]
        elif "M" in value:
            return AWSClient._from_dynamodb_item(value["M"])
        elif "SS" in value:
            return set(value["SS"])
        elif "NS" in value:
            return {
                float(n) if "." in n else int(n) for n in value["NS"]
            }
        else:
            return value

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------
    async def health_check(self) -> Dict[str, Any]:
        """
        Verify AWS connectivity by calling STS GetCallerIdentity.

        Returns:
            Dict with 'healthy' boolean and identity/error details.
        """
        try:
            identity = await self.sts_get_caller_identity()
            return {
                "healthy": True,
                "region": self.region,
                "account": identity.get("account"),
                "arn": identity.get("arn"),
                "mock_mode": self.mock_mode,
            }
        except Exception as e:
            logger.error("AWS health check failed: %s", str(e))
            return {
                "healthy": False,
                "region": self.region,
                "error": str(e),
                "mock_mode": self.mock_mode,
            }

    # ------------------------------------------------------------------
    # Context Manager
    # ------------------------------------------------------------------
    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit. Clean up clients."""
        self._s3_client = None
        self._dynamodb_client = None
        self._dynamodb_resource = None
        self._sts_client = None
        self._session = None

    def __repr__(self) -> str:
        return (
            f"AWSClient(region={self.region!r}, "
            f"mock_mode={self.mock_mode}, "
            f"profile={self.profile!r})"
        )
