# ADR-001: FastAPI as API Framework for LLM Inference Platform

**Status:** ACCEPTED
**Date:** 2026-02-21
**Author:** Gopi Krishna Vajrala
**Deciders:** Platform Engineering Team
**Category:** API Layer

---

## Context

The Netflix Real-Time LLM Personalization & Inference Platform requires a high-performance API layer that can handle 50,000+ requests per second globally with sub-100ms P99 latency. The API layer must support:

- Asynchronous request handling for non-blocking GPU inference calls
- Server-Sent Events (SSE) for streaming token-by-token responses
- High concurrency with minimal overhead per connection
- Native support for gRPC communication with NVIDIA Triton Inference Server
- Automatic API documentation for internal consumers
- Pydantic-based request/response validation for type safety
- Middleware support for authentication, rate limiting, and circuit breaking

The API layer sits between client applications (Netflix apps, internal services) and the GPU inference backend (Triton). It must efficiently manage the latency budget: of the 100ms P99 budget, only 10ms is allocated to request parsing, routing, and response serialization.

## Decision

We adopt **FastAPI** as the primary API framework for the LLM inference platform.

## Alternatives Considered

### Flask

- **Pros:** Mature ecosystem, simple, well-understood by team
- **Cons:** Synchronous by default (requires extensions for async), no native SSE support, no automatic OpenAPI generation, WSGI-based architecture limits concurrency
- **Rejected because:** The synchronous request model would create thread contention under high concurrency. Each GPU inference call takes 30-60ms; blocking threads during this time would require hundreds of threads to maintain throughput, adding significant memory overhead and context-switching costs.

### Django + Django REST Framework

- **Pros:** Batteries-included, ORM, admin interface, mature
- **Cons:** Heavy framework overhead (15-20ms per request), synchronous by default (ASGI support is recent and less mature), ORM unnecessary for inference workload, significantly slower than FastAPI for I/O-bound workloads
- **Rejected because:** Framework overhead alone would consume 15-20% of the latency budget. The ORM and admin features are unnecessary for an inference-focused API.

### gRPC-only (No REST)

- **Pros:** Lower serialization overhead (Protocol Buffers), native streaming, strongly typed contracts, excellent performance
- **Cons:** Poor browser support (requires gRPC-Web proxy), debugging difficulty (binary protocol), limited tooling for non-gRPC clients, Netflix mobile apps use REST
- **Rejected as sole solution because:** While we use gRPC internally (FastAPI to Triton), the external API must support REST for compatibility with Netflix client applications. FastAPI provides both REST and can proxy to gRPC backends.

## Consequences

### Positive

- **Native async/await:** FastAPI runs on uvicorn (ASGI), enabling true async I/O. GPU inference calls are non-blocking, allowing a single worker to handle hundreds of concurrent requests.
- **SSE streaming support:** Native support for `StreamingResponse` enables token-by-token delivery without additional libraries.
- **Automatic OpenAPI docs:** Swagger UI and ReDoc are generated automatically from Pydantic models, reducing documentation burden.
- **Pydantic validation:** Request/response models are validated at the framework level with zero additional code, catching malformed requests before they reach the inference pipeline.
- **Performance:** FastAPI benchmarks at 15,000+ requests/second per worker on CPU-bound tasks, and significantly higher for async I/O workloads.
- **Type safety:** Python type hints provide IDE support, catch errors at development time, and generate accurate API contracts.
- **Middleware ecosystem:** Built-in support for CORS, trusted hosts, GZip compression, and custom middleware for rate limiting and circuit breaking.

### Negative

- **Python GIL:** CPU-bound operations (tokenization, response formatting) are limited by the GIL. Mitigated by offloading CPU-intensive work to separate processes and using uvloop for event loop optimization.
- **Memory footprint:** Each uvicorn worker consumes ~200MB. With 8 workers per pod, this totals ~1.6GB per pod. Acceptable given the p4d.24xlarge instances have 1,152 GB RAM.
- **Learning curve:** Team members familiar with Flask/Django need to learn async patterns and Pydantic models. Mitigated with internal training and code examples.

### Performance Configuration

```python
# Production uvicorn configuration
uvicorn_config = {
    "host": "0.0.0.0",
    "port": 8080,
    "workers": 8,               # Match CPU cores allocated to API pod
    "loop": "uvloop",           # High-performance event loop
    "http": "httptools",        # Fast HTTP parser
    "limit_concurrency": 1000,  # Max concurrent connections per worker
    "timeout_keep_alive": 30,   # Keep-alive timeout
    "access_log": False,        # Disable for performance (use structured logging)
}
```

---

**References:**

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Uvicorn ASGI Server](https://www.uvicorn.org/)
- [NVIDIA Triton Client Libraries](https://github.com/triton-inference-server/client)
