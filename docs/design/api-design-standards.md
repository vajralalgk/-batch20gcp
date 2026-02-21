<div align="center">

# ECTP API Design Standards

```
╔══════════════════════════════════════════════════════════════════╗
║                  ECTP API DESIGN STANDARDS                      ║
║               Enterprise Cloud Transformation Platform          ║
╚══════════════════════════════════════════════════════════════════╝
```

**Author:** Gopi Krishna Vajrala
**Version:** 1.0.0
**Last Updated:** 2026-02-16

</div>

---

## API Request/Response Flow

```
API REQUEST/RESPONSE LIFECYCLE
══════════════════════════════════════════════════════════════════════════════

  Client                    API Gateway              Backend Service
    │                           │                          │
    │   HTTP Request            │                          │
    │   + Bearer Token          │                          │
    │ ─────────────────────►    │                          │
    │                           │  Auth + Rate Limit       │
    │                           │  Validation              │
    │                           │ ────────────────────►    │
    │                           │                          │  Process
    │                           │                          │  Request
    │                           │    JSON Response         │
    │                           │ ◄────────────────────    │
    │   HTTP Response           │                          │
    │   + Status Code           │                          │
    │   + Rate Limit Headers    │                          │
    │ ◄─────────────────────    │                          │
    │                           │                          │

══════════════════════════════════════════════════════════════════════════════
```

---

## URL Structure

> **Convention:** All endpoints follow RESTful naming with URL-based versioning

```
BASE PATTERN:  /api/v{version}/{resource}
══════════════════════════════════════════════════════════════

  /api/v1/migration/plans          Migration planning
  /api/v1/servicenow/incidents     ServiceNow integration
  /api/v1/ellucian/students        Ellucian student data
  /api/v1/cost/summary             Cost governance

══════════════════════════════════════════════════════════════
```

| Component | Convention | Example |
|-----------|-----------|---------|
| Base path | `/api` | `/api/...` |
| Version | `/v{major}` | `/api/v1/...` |
| Resource | Plural nouns, lowercase | `/api/v1/migration/plans` |
| Sub-resource | Nested path | `/api/v1/plans/{id}/tasks` |

---

## HTTP Methods

> Each method is color-coded by its operational semantics

| | Method | Usage | Idempotent | Request Body | Typical Response |
|---|---|---|---|---|---|
| :green_circle: | **`GET`** | Read resource(s) | Yes | No | `200 OK` |
| :blue_circle: | **`POST`** | Create resource | No | Yes | `201 Created` |
| :orange_circle: | **`PUT`** | Full update (replace) | Yes | Yes | `200 OK` |
| :yellow_circle: | **`PATCH`** | Partial update | Yes | Yes | `200 OK` |
| :red_circle: | **`DELETE`** | Remove resource | Yes | No | `204 No Content` |

```
HTTP METHODS VISUAL GUIDE
══════════════════════════════════════════════════════════════════

  GET     ■■■■■■■■■■  Read-only, safe, cacheable
  POST    ■■■■■■■■■■  Creates new resource, not idempotent
  PUT     ■■■■■■■■■■  Full replacement, idempotent
  PATCH   ■■■■■■■■■■  Partial update, idempotent
  DELETE  ■■■■■■■■■■  Removes resource, idempotent

══════════════════════════════════════════════════════════════════
```

### Usage Examples

<details>
<summary><strong>:green_circle: GET - Read Resources</strong></summary>

```bash
# List all migration plans
GET /api/v1/migration/plans

# Get a specific plan
GET /api/v1/migration/plans/123
```

</details>

<details>
<summary><strong>:blue_circle: POST - Create Resource</strong></summary>

```bash
# Create a new migration plan
POST /api/v1/migration/plans
Content-Type: application/json

{
    "name": "Phase 2 Migration",
    "target_date": "2026-06-01"
}
```

</details>

<details>
<summary><strong>:orange_circle: PUT - Full Update</strong></summary>

```bash
# Replace an entire migration plan
PUT /api/v1/migration/plans/123
Content-Type: application/json

{
    "name": "Phase 2 Migration (Updated)",
    "target_date": "2026-07-01",
    "status": "in_progress"
}
```

</details>

<details>
<summary><strong>:red_circle: DELETE - Remove Resource</strong></summary>

```bash
# Delete a migration plan
DELETE /api/v1/migration/plans/123

# Response: 204 No Content
```

</details>

---

## Response Format

> **All API responses follow a consistent envelope structure**

### Success Response

```json
{
    "data": {
        "id": "plan-123",
        "name": "Phase 2 Migration",
        "status": "active"
    },
    "meta": {
        "timestamp": "2026-02-16T12:00:00Z",
        "correlation_id": "abc123"
    }
}
```

### Error Response

```json
{
    "error_code": "NOT_FOUND",
    "message": "Resource not found",
    "details": {
        "resource": "migration_plan",
        "id": "plan-999"
    }
}
```

```
RESPONSE ENVELOPE STRUCTURE
══════════════════════════════════════════════════════════════

  Success Response              Error Response
  ┌───────────────────┐         ┌───────────────────┐
  │ {                 │         │ {                 │
  │   "data": {...},  │         │   "error_code":   │
  │   "meta": {       │         │     "NOT_FOUND",  │
  │     "timestamp",  │         │   "message":      │
  │     "correlation  │         │     "Resource...",│
  │      _id"         │         │   "details": {}   │
  │   }               │         │ }                 │
  │ }                 │         └───────────────────┘
  └───────────────────┘

══════════════════════════════════════════════════════════════
```

---

## Status Codes

### :green_circle: 2xx - Success

| Code | Name | Usage | When to Use |
|------|------|-------|-------------|
| `200` | OK | Success | GET, PUT, PATCH success |
| `201` | Created | Resource created | POST success |
| `204` | No Content | Deleted | DELETE success |

### :orange_circle: 4xx - Client Error

| Code | Name | Usage | When to Use |
|------|------|-------|-------------|
| `400` | Bad Request | Invalid input | Malformed request body |
| `401` | Unauthorized | Not authenticated | Missing or invalid token |
| `403` | Forbidden | Not authorized | Insufficient permissions |
| `404` | Not Found | Resource missing | Resource does not exist |
| `422` | Validation Error | Business rule violation | Data validation failure |
| `429` | Rate Limited | Too many requests | Rate limit exceeded |

### :red_circle: 5xx - Server Error

| Code | Name | Usage | When to Use |
|------|------|-------|-------------|
| `500` | Internal Error | Server failure | Unexpected server error |
| `502` | External Service Error | Upstream failure | ServiceNow, Ellucian down |

```
STATUS CODE DECISION TREE
══════════════════════════════════════════════════════════════

  Request Received
       │
       ├── Authenticated? ──No──► 401 Unauthorized
       │
       ├── Authorized? ──No──► 403 Forbidden
       │
       ├── Valid Input? ──No──► 400 Bad Request
       │
       ├── Resource Exists? ──No──► 404 Not Found
       │
       ├── Business Rules Pass? ──No──► 422 Validation Error
       │
       ├── Rate Limit OK? ──No──► 429 Rate Limited
       │
       ├── Server OK? ──No──► 500 Internal Error
       │
       ├── Upstream OK? ──No──► 502 External Service Error
       │
       └── Success ──► 200 / 201 / 204

══════════════════════════════════════════════════════════════
```

---

## Pagination

> **Offset-based pagination for list endpoints**

```
PAGINATION FLOW
══════════════════════════════════════════════════════════════

  Page 1              Page 2              Page 3
  offset=0            offset=50           offset=100
  limit=50            limit=50            limit=50
  ┌──────────┐        ┌──────────┐        ┌──────────┐
  │ Items    │        │ Items    │        │ Items    │
  │ 1 - 50   │───────►│ 51 - 100 │───────►│101 - 150 │
  └──────────┘        └──────────┘        └──────────┘

══════════════════════════════════════════════════════════════
```

### Request

```
GET /api/v1/migration/plans?limit=50&offset=100
```

### Pagination Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 50 | Number of items per page (max 100) |
| `offset` | integer | 0 | Number of items to skip |

### Response with Pagination Metadata

```json
{
    "data": [...],
    "meta": {
        "timestamp": "2026-02-16T12:00:00Z",
        "correlation_id": "abc123",
        "pagination": {
            "limit": 50,
            "offset": 100,
            "total": 250
        }
    }
}
```

---

## Versioning

> **URL-based versioning strategy for breaking changes only**

```
API VERSIONING STRATEGY
══════════════════════════════════════════════════════════════

  /api/v1/...    CURRENT ──── Actively maintained
                                │
                                │  12-month overlap
                                │
  /api/v2/...    NEXT    ──── Breaking changes only
                                │
                                │  After 12 months
                                │
  /api/v1/...    SUNSET  ──── Deprecated, then removed

══════════════════════════════════════════════════════════════
```

| Rule | Description |
|------|-------------|
| **URL-based** | Versioning via URL path (`/api/v1/`, `/api/v2/`) |
| **Major versions only** | Only for breaking changes |
| **Deprecation period** | Old versions supported for **12 months** after deprecation |
| **Deprecation headers** | `Sunset` and `Deprecation` headers on deprecated versions |

---

## Authentication

> **Multi-layer authentication supporting user and service-to-service calls**

```
AUTHENTICATION FLOW
══════════════════════════════════════════════════════════════════

  User Authentication (SAML 2.0 SSO)
  ────────────────────────────────────
  Browser ──► IdP (SAML) ──► Cognito ──► JWT Token ──► API

  API Authentication (Bearer Token)
  ──────────────────────────────────
  Client ──► Authorization: Bearer <JWT> ──► API Gateway ──► Validate

  Service-to-Service (API Key)
  ────────────────────────────
  Service A ──► X-API-Key: <key> ──► API Gateway ──► Service B

══════════════════════════════════════════════════════════════════
```

| Method | Use Case | Header | Token Type |
|--------|----------|--------|------------|
| **Bearer Token (JWT)** | User API calls | `Authorization: Bearer <token>` | JWT via Cognito |
| **API Keys** | Service-to-service | `X-API-Key: <key>` | Managed API key |
| **SAML 2.0** | SSO federation | Browser redirect | SAML assertion |

### Example Headers

```http
# User API call
GET /api/v1/migration/plans HTTP/1.1
Host: api.ectp.edu
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...
Content-Type: application/json

# Service-to-service call
GET /api/v1/servicenow/incidents HTTP/1.1
Host: api.ectp.edu
X-API-Key: ectp-svc-key-abc123
Content-Type: application/json
```

---

## Rate Limiting

> **Protect API availability with per-client rate limits**

```
RATE LIMITING VISUAL
══════════════════════════════════════════════════════════════

  Client Requests per Minute (1000 max)
  ──────────────────────────────────────

  0        250       500       750      1000
  ├─────────┼─────────┼─────────┼─────────┤
  │ ■■■■■■■■■■■■■■■■■■■■■■■■■  │         │
  │         GREEN (OK)          │ YELLOW  │  RED
  │         (0-750)             │(751-999)│ (1000+)
  │                             │ Warning │  429
  └─────────────────────────────┴─────────┘

══════════════════════════════════════════════════════════════
```

| Parameter | Value |
|-----------|-------|
| **Rate limit** | 1000 requests per minute per client |
| **Exceeded response** | `429 Too Many Requests` |
| **Retry header** | `Retry-After: <seconds>` |
| **Limit headers** | Included in every response |

### Rate Limit Response Headers

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 742
X-RateLimit-Reset: 1708099200

# When rate limited:
HTTP/1.1 429 Too Many Requests
Retry-After: 30
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1708099200
```

---

<div align="center">

```
══════════════════════════════════════════════════════════════
                END OF API DESIGN STANDARDS
            Enterprise Cloud Transformation Platform
══════════════════════════════════════════════════════════════
```

**Author:** Gopi Krishna Vajrala

</div>
