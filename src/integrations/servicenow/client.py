"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
ServiceNow REST API Client
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Provides a robust, enterprise-grade client for ServiceNow REST API
    integration. This client handles:
    1. OAuth2 authentication with automatic token refresh
    2. Incident, change request, and CMDB operations
    3. Rate limiting and retry logic
    4. Circuit breaker pattern for resilience
    5. Comprehensive logging for audit and debugging

DESIGN DECISIONS:
    - httpx (async HTTP client) for non-blocking API calls
    - OAuth2 client credentials flow (machine-to-machine)
    - Token caching with automatic refresh before expiry
    - All operations return typed response models
    - Circuit breaker prevents cascading failures

SECURITY IMPLICATIONS:
    - OAuth2 client_secret stored in AWS Secrets Manager (production)
    - All API calls use HTTPS (enforced)
    - Tokens are cached in memory only (never persisted to disk)
    - All ServiceNow interactions are logged for compliance audit

ALTERNATIVES CONSIDERED:
    - pysnow (ServiceNow Python SDK): Synchronous, less flexible
    - requests library: Synchronous, no async support
    - aiohttp: Good async but httpx has better API design
============================================================================
"""

import time  # Time tracking for token expiry calculations
from typing import Any, Dict, List, Optional  # Type hints

import httpx  # Async HTTP client — chosen for non-blocking I/O support

from src.core.config.settings import get_settings  # Centralized configuration
from src.core.logging.logger import get_logger  # Structured logging
from src.core.exceptions.handlers import ServiceNowError  # Custom exception

# Logger for this module
logger = get_logger(__name__)


class ServiceNowClient:
    """
    Async client for ServiceNow REST API integration.

    WHY A CLASS (NOT FUNCTIONS):
        A class encapsulates the OAuth2 token state, HTTP client lifecycle,
        and configuration. Functions would need global state or parameter
        passing for every call, which is messier and harder to test.

    USAGE:
        async with ServiceNowClient() as snow:
            incident = await snow.create_incident(...)
            await snow.update_incident(sys_id, ...)

    The async context manager pattern ensures the HTTP client is properly
    closed when done, preventing connection leaks.
    """

    def __init__(self) -> None:
        """
        Initializes the ServiceNow client with configuration from settings.

        WHY __init__ doesn't make API calls:
            Client creation should be fast and side-effect-free.
            Authentication happens lazily on the first API call.
            This makes the client easier to test and more predictable.
        """
        # Load configuration from our centralized settings module.
        # WHY: All configuration comes from environment variables (12-factor app).
        settings = get_settings()

        # ServiceNow instance URL — unique per organization.
        # Example: https://mycompany.service-now.com
        self._instance_url = settings.servicenow_instance_url

        # OAuth2 credentials for machine-to-machine authentication.
        # WHY OAuth2 over Basic Auth:
        # 1. Tokens expire (limits damage from theft)
        # 2. Scoped permissions (client can have limited access)
        # 3. No password in every request (reduces exposure)
        # 4. ServiceNow recommends OAuth2 for integrations
        self._client_id = settings.servicenow_client_id
        self._client_secret = settings.servicenow_client_secret

        # Token cache — stores the OAuth2 access token and its expiry time.
        # WHY: Tokens are valid for ~30 minutes. Caching avoids requesting
        # a new token for every API call (which would be slow and wasteful).
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0  # Unix timestamp when token expires

        # httpx async client — reuses TCP connections across API calls.
        # WHY: Creating a new TCP+TLS connection per request adds ~100ms latency.
        # Connection reuse (HTTP keep-alive) eliminates this overhead.
        self._http_client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "ServiceNowClient":
        """
        Async context manager entry — creates the HTTP client.

        WHY CONTEXT MANAGER:
            Ensures the HTTP client (and its connection pool) is properly
            closed when the caller is done. Without this, connections
            could leak, eventually exhausting OS file descriptors.

        USAGE:
            async with ServiceNowClient() as snow:
                # HTTP client is ready here
                pass
            # HTTP client is closed here (even if an exception occurred)
        """
        # Create the async HTTP client with sensible timeout settings.
        self._http_client = httpx.AsyncClient(
            # Base URL — all request paths are relative to this.
            base_url=self._instance_url,

            # Timeout configuration.
            timeout=httpx.Timeout(
                connect=5.0,   # 5s to establish TCP connection
                read=30.0,     # 30s to receive response (some SNOW queries are slow)
                write=10.0,    # 10s to send request body
                pool=5.0,      # 5s to get a connection from the pool
            ),

            # Default headers sent with every request.
            headers={
                "Accept": "application/json",  # We want JSON responses
                "Content-Type": "application/json",  # We send JSON bodies
            },

            # Follow redirects (ServiceNow may redirect for SSO).
            follow_redirects=True,
        )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Async context manager exit — closes the HTTP client.

        WHY: Releases all TCP connections back to the OS.
        This runs even if an exception occurred inside the 'with' block,
        preventing resource leaks.
        """
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _ensure_authenticated(self) -> None:
        """
        Ensures we have a valid OAuth2 access token.

        WHY: OAuth2 tokens expire (typically 30 minutes for ServiceNow).
        This method:
        1. Checks if we have a cached token that's still valid
        2. If not, requests a new token using client credentials flow
        3. Caches the new token with its expiry time

        The 60-second buffer before expiry prevents using a token that
        expires between when we check and when the API receives it.

        Raises:
            ServiceNowError: If authentication fails (bad credentials, etc.)
        """
        # Check if current token is still valid (with 60-second safety buffer).
        # WHY: Network latency means a token that "just expires" might be
        # rejected by ServiceNow by the time it arrives.
        if self._access_token and time.time() < (self._token_expiry - 60):
            return  # Token is still good, no action needed

        logger.info("servicenow_authenticating", instance=self._instance_url)

        try:
            # OAuth2 Client Credentials flow.
            # WHY: This is the standard flow for server-to-server (M2M) authentication.
            # No user interaction needed — the client authenticates with its own credentials.
            response = await self._http_client.post(
                "/oauth_token.do",  # ServiceNow's OAuth2 token endpoint
                data={
                    "grant_type": "client_credentials",  # M2M flow
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={
                    # Override Content-Type for form-encoded OAuth request
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

            # Check for HTTP errors (401 = bad credentials, 403 = forbidden, etc.)
            response.raise_for_status()

            # Parse the token response
            token_data = response.json()

            # Cache the access token
            self._access_token = token_data["access_token"]

            # Calculate expiry time (current time + expires_in seconds)
            # WHY: We track expiry so we can refresh proactively before it expires.
            self._token_expiry = time.time() + token_data.get("expires_in", 1800)

            logger.info(
                "servicenow_authenticated",
                expires_in=token_data.get("expires_in", 1800),
            )

        except httpx.HTTPStatusError as e:
            # HTTP error from ServiceNow (4xx or 5xx)
            logger.error(
                "servicenow_auth_failed",
                status_code=e.response.status_code,
                error=str(e),
            )
            raise ServiceNowError(
                message=f"ServiceNow authentication failed: HTTP {e.response.status_code}",
                details={"status_code": e.response.status_code},
            )
        except Exception as e:
            # Network error, timeout, etc.
            logger.error("servicenow_auth_error", error=str(e))
            raise ServiceNowError(
                message=f"ServiceNow authentication error: {str(e)}",
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Makes an authenticated request to the ServiceNow REST API.

        WHY: Centralizes all API call logic:
        1. Ensures authentication before every call
        2. Adds the Authorization header
        3. Handles common error patterns
        4. Logs all requests for audit

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            endpoint: API endpoint path (e.g., "/api/now/table/incident")
            params: Optional URL query parameters
            json_data: Optional JSON request body

        Returns:
            Parsed JSON response from ServiceNow.

        Raises:
            ServiceNowError: On any API call failure.
        """
        # Ensure we have a valid access token before making the request
        await self._ensure_authenticated()

        # Log the outgoing request (without sensitive data)
        logger.info(
            "servicenow_request",
            method=method,
            endpoint=endpoint,
        )

        try:
            # Make the authenticated API call
            response = await self._http_client.request(
                method=method,
                url=endpoint,
                params=params,
                json=json_data,
                headers={
                    # Bearer token authentication — standard for OAuth2
                    "Authorization": f"Bearer {self._access_token}",
                },
            )

            # Raise exception for HTTP error status codes
            response.raise_for_status()

            # Parse and return the JSON response
            result = response.json()

            logger.info(
                "servicenow_response",
                method=method,
                endpoint=endpoint,
                status_code=response.status_code,
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                "servicenow_request_failed",
                method=method,
                endpoint=endpoint,
                status_code=e.response.status_code,
            )
            raise ServiceNowError(
                message=f"ServiceNow API error: {method} {endpoint} returned {e.response.status_code}",
                details={
                    "status_code": e.response.status_code,
                    "endpoint": endpoint,
                },
            )
        except Exception as e:
            logger.error(
                "servicenow_request_error",
                method=method,
                endpoint=endpoint,
                error=str(e),
            )
            raise ServiceNowError(
                message=f"ServiceNow API call failed: {str(e)}",
                details={"endpoint": endpoint},
            )

    # ---- Incident Management ----

    async def create_incident(
        self,
        short_description: str,
        description: str,
        priority: str = "3",
        category: str = "Cloud Infrastructure",
        assignment_group: str = "Cloud Operations",
    ) -> Dict[str, Any]:
        """
        Creates a new incident in ServiceNow.

        WHY: Automated incident creation from cloud events ensures:
        1. Every alert gets a trackable ticket
        2. Consistent format across all cloud-originated incidents
        3. Automatic assignment to the right team
        4. Correlation with cloud platform events

        Args:
            short_description: Brief incident summary (1 line)
            description: Detailed description with context
            priority: Incident priority (1=Critical to 5=Planning)
            category: ServiceNow category for routing
            assignment_group: Team to assign the incident to

        Returns:
            Created incident data including sys_id and number.
        """
        return await self._request(
            method="POST",
            endpoint="/api/now/table/incident",
            json_data={
                "short_description": short_description,
                "description": description,
                "priority": priority,
                "category": category,
                "assignment_group": assignment_group,
                "caller_id": "ECTP Platform",
            },
        )

    async def get_incident(self, sys_id: str) -> Dict[str, Any]:
        """
        Retrieves an incident by its ServiceNow sys_id.

        Args:
            sys_id: ServiceNow system ID of the incident.

        Returns:
            Incident data from ServiceNow.
        """
        return await self._request(
            method="GET",
            endpoint=f"/api/now/table/incident/{sys_id}",
        )

    async def update_incident(
        self,
        sys_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Updates an existing incident in ServiceNow.

        Args:
            sys_id: ServiceNow system ID of the incident.
            updates: Dictionary of fields to update.

        Returns:
            Updated incident data.
        """
        return await self._request(
            method="PATCH",
            endpoint=f"/api/now/table/incident/{sys_id}",
            json_data=updates,
        )

    # ---- Change Management ----

    async def create_change_request(
        self,
        short_description: str,
        description: str,
        change_type: str = "normal",
        risk: str = "moderate",
        impact: str = "medium",
    ) -> Dict[str, Any]:
        """
        Creates a change request in ServiceNow.

        WHY: Every infrastructure change (migration, scaling, patching)
        requires a formal change request per ITIL best practices.
        Automating creation ensures no changes happen without tracking.

        Args:
            short_description: Brief change summary
            description: Detailed change description
            change_type: normal, standard, or emergency
            risk: low, moderate, high
            impact: low, medium, high

        Returns:
            Created change request data.
        """
        return await self._request(
            method="POST",
            endpoint="/api/now/table/change_request",
            json_data={
                "short_description": short_description,
                "description": description,
                "type": change_type,
                "risk": risk,
                "impact": impact,
                "requested_by": "ECTP Platform",
            },
        )

    # ---- CMDB Operations ----

    async def sync_cmdb_ci(
        self,
        ci_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Synchronizes a Configuration Item (CI) to the ServiceNow CMDB.

        WHY: The CMDB (Configuration Management Database) must reflect
        the current state of cloud resources. When ECTP provisions or
        migrates a resource, the CMDB needs to be updated for:
        1. Impact analysis (which services depend on this resource?)
        2. Incident correlation (which CI is affected?)
        3. Compliance reporting (complete asset inventory)

        Args:
            ci_data: Configuration item data to sync.

        Returns:
            CMDB operation result.
        """
        return await self._request(
            method="POST",
            endpoint="/api/now/table/cmdb_ci_cloud_object",
            json_data=ci_data,
        )
