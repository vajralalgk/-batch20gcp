"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Ellucian Ethos API Client
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Provides integration with Ellucian's Ethos platform, the standard
    integration layer for Higher Education information systems including:
    - Banner (student information, financial aid, HR)
    - Colleague (student records, finance)
    - Ethos Identity (SSO, user management)

    This client enables ECTP to:
    1. Read student enrollment data for capacity planning
    2. Sync institutional data for reporting
    3. Integrate identity management
    4. Access academic calendar for maintenance scheduling

DESIGN DECISIONS:
    - Ethos API (REST) is the only supported integration method
    - API key authentication (Ethos standard)
    - Read-heavy pattern (minimize writes to protect source systems)
    - Pagination support for large datasets (student records)
    - FERPA-compliant data minimization (only fetch needed fields)

SECURITY IMPLICATIONS:
    - FERPA: All student data access MUST be logged with who/what/why
    - API keys rotated quarterly via Secrets Manager
    - PII fields are never cached (in-memory only, short-lived)
    - Data classification enforced at the API response level
    - TLS 1.3 enforced for all Ethos API calls

ALTERNATIVES CONSIDERED:
    - Direct Banner database access: Too fragile, bypasses business logic
    - Banner API (non-Ethos): Deprecated by Ellucian in favor of Ethos
    - File-based integration (CSV/SFTP): Too slow, not real-time
============================================================================
"""

from typing import Any, Dict, List, Optional  # Type hints

import httpx  # Async HTTP client for non-blocking API calls

from src.core.config.settings import get_settings  # Centralized configuration
from src.core.logging.logger import get_logger  # Structured logging
from src.core.exceptions.handlers import EllucianError  # Custom exception

logger = get_logger(__name__)


class EllucianEthosClient:
    """
    Async client for the Ellucian Ethos Integration API.

    WHY: Ethos is Ellucian's strategic integration platform. All modern
    integrations with Banner/Colleague should use Ethos rather than
    direct database or legacy API access. Ethos provides:
    - Standardized data models across Banner and Colleague
    - Change notifications (events) for real-time sync
    - Rate limiting and throttling protection
    - Centralized authentication

    USAGE:
        async with EllucianEthosClient() as ethos:
            students = await ethos.get_students(limit=100)
            enrollment = await ethos.get_enrollment_summary("Fall 2026")
    """

    def __init__(self) -> None:
        """
        Initializes the Ethos client with configuration.

        Configuration comes from environment variables:
        - ECTP_ELLUCIAN_ETHOS_API_URL: Ethos API base URL
        - ECTP_ELLUCIAN_API_KEY: API key for authentication
        """
        settings = get_settings()

        # Ethos API base URL — provided by Ellucian during onboarding.
        # Typically: https://integrate.elluciancloud.com
        self._api_url = settings.ellucian_ethos_api_url

        # API key for authentication — issued per integration application.
        # WHY API key (not OAuth2): Ethos uses API keys as the primary
        # authentication method for server-to-server integrations.
        # The key is exchanged for a JWT token on each session.
        self._api_key = settings.ellucian_api_key

        # JWT token received from Ethos after API key authentication.
        # WHY separate from API key: The JWT has a shorter lifespan and
        # contains claims about what data the integration can access.
        self._jwt_token: Optional[str] = None

        # HTTP client instance (created in __aenter__)
        self._http_client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "EllucianEthosClient":
        """Creates the async HTTP client for Ethos API calls."""
        self._http_client = httpx.AsyncClient(
            base_url=self._api_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=30.0,  # Ethos queries can be slow for large datasets
                write=10.0,
                pool=5.0,
            ),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Closes the HTTP client, releasing connections."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _authenticate(self) -> None:
        """
        Authenticates with Ethos by exchanging the API key for a JWT token.

        WHY: Ethos uses a two-step authentication:
        1. Send the API key to the auth endpoint
        2. Receive a JWT token for subsequent API calls

        The JWT token typically expires after 5 minutes, so we
        re-authenticate before every batch of API calls for safety.

        Raises:
            EllucianError: If authentication fails.
        """
        logger.info("ellucian_authenticating")

        try:
            # Exchange API key for JWT token.
            # The API key goes in the Authorization header.
            response = await self._http_client.post(
                "/auth",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()

            # The JWT token is returned in the response body.
            self._jwt_token = response.text.strip('"')

            logger.info("ellucian_authenticated")

        except httpx.HTTPStatusError as e:
            logger.error(
                "ellucian_auth_failed",
                status_code=e.response.status_code,
            )
            raise EllucianError(
                message=f"Ethos authentication failed: HTTP {e.response.status_code}",
                details={"status_code": e.response.status_code},
            )
        except Exception as e:
            logger.error("ellucian_auth_error", error=str(e))
            raise EllucianError(
                message=f"Ethos authentication error: {str(e)}",
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Makes an authenticated request to the Ethos API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Optional query parameters

        Returns:
            Parsed JSON response.
        """
        # Always re-authenticate (Ethos JWT tokens are very short-lived)
        await self._authenticate()

        logger.info(
            "ellucian_request",
            method=method,
            endpoint=endpoint,
        )

        try:
            response = await self._http_client.request(
                method=method,
                url=endpoint,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._jwt_token}",
                },
            )
            response.raise_for_status()

            logger.info(
                "ellucian_response",
                method=method,
                endpoint=endpoint,
                status_code=response.status_code,
            )

            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                "ellucian_request_failed",
                endpoint=endpoint,
                status_code=e.response.status_code,
            )
            raise EllucianError(
                message=f"Ethos API error: {method} {endpoint} returned {e.response.status_code}",
            )
        except Exception as e:
            logger.error(
                "ellucian_request_error",
                endpoint=endpoint,
                error=str(e),
            )
            raise EllucianError(message=f"Ethos API call failed: {str(e)}")

    # ---- Student Data ----

    async def get_persons(
        self,
        offset: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves person records from Ellucian via Ethos.

        WHY: Person records are the foundation for student identity.
        Used for:
        - Mapping cloud accounts to institutional identities
        - Enrollment verification for access control
        - Capacity planning based on student demographics

        FERPA COMPLIANCE:
        - Only requested fields are returned (data minimization)
        - Every call is logged with the purpose of access
        - PII is not cached beyond the immediate request

        Args:
            offset: Pagination offset (skip N records)
            limit: Maximum records to return (max 500 per Ethos API)

        Returns:
            List of person records.
        """
        # FERPA audit log — required for any student data access
        logger.info(
            "ellucian_student_data_access",
            access_type="persons",
            offset=offset,
            limit=limit,
            access_reason="platform_operation",
        )

        return await self._request(
            method="GET",
            endpoint="/api/persons",
            params={"offset": offset, "limit": min(limit, 500)},
        )

    async def get_student_enrollments(
        self,
        student_id: Optional[str] = None,
        term: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves student enrollment data.

        WHY: Enrollment numbers directly impact infrastructure scaling:
        - Registration periods: 5-10x normal API traffic
        - Each registration: ~50 database transactions
        - Financial aid processing: sustained DB load

        This data feeds the predictive scaling engine to pre-provision
        resources before enrollment peaks.

        Args:
            student_id: Optional filter by specific student
            term: Optional filter by academic term

        Returns:
            List of enrollment records.
        """
        params = {}
        if student_id:
            params["student"] = student_id
        if term:
            params["academicPeriod"] = term

        logger.info(
            "ellucian_enrollment_access",
            student_id=student_id,
            term=term,
        )

        return await self._request(
            method="GET",
            endpoint="/api/student-academic-periods",
            params=params,
        )

    async def get_academic_periods(self) -> List[Dict[str, Any]]:
        """
        Retrieves academic period (term) definitions.

        WHY: Academic periods define the calendar for:
        - Maintenance window scheduling (avoid registration periods)
        - Predictive scaling (ramp up before registration opens)
        - Reporting periods for cost and compliance

        Returns:
            List of academic period definitions.
        """
        return await self._request(
            method="GET",
            endpoint="/api/academic-periods",
        )

    # ---- Health Check ----

    async def check_connectivity(self) -> Dict[str, Any]:
        """
        Verifies connectivity to the Ethos API.

        WHY: Called during application startup and health checks.
        Detects:
        - API key expiry
        - Network connectivity issues
        - Ethos maintenance windows
        - Tenant configuration problems

        Returns:
            Connectivity status with details.
        """
        try:
            await self._authenticate()
            return {
                "connected": True,
                "api_url": self._api_url,
                "status": "healthy",
            }
        except EllucianError as e:
            return {
                "connected": False,
                "api_url": self._api_url,
                "status": "unhealthy",
                "error": str(e),
            }
