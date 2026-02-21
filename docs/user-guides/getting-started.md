<div align="center">

# Getting Started with ECTP

```
╔══════════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║      ███████╗  ██████╗ ████████╗ ██████╗                               ║
║      ██╔════╝ ██╔════╝ ╚══██╔══╝ ██╔══██╗                             ║
║      █████╗   ██║         ██║    ██████╔╝                              ║
║      ██╔══╝   ██║         ██║    ██╔═══╝                               ║
║      ███████╗ ╚██████╗    ██║    ██║                                   ║
║      ╚══════╝  ╚═════╝   ╚═╝    ╚═╝                                   ║
║                                                                        ║
║          Enterprise Cloud Transformation Platform                      ║
║                                                                        ║
║              Your journey to cloud excellence starts here              ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════════╝
```

![Version](https://img.shields.io/badge/version-1.0.0-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Required-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-Internal-red?style=for-the-badge)

**Author:** Gopi Krishna Vajrala | **Audience:** New team members, developers, and operators

---

*Estimated Setup Time:* **~5 minutes**

</div>

---

## Welcome to ECTP

The **Enterprise Cloud Transformation Platform (ECTP)** is your organization's unified platform for cloud infrastructure, IT service management, and Higher Education system integration.

> **What you'll accomplish in this guide:**
> - Set up your local development environment
> - Run the ECTP API server locally
> - Access interactive API documentation
> - Understand where to go next

---

## Quick Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        ECTP Architecture at a Glance                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌──────────┐     ┌──────────────┐     ┌──────────────────────────┐   │
│   │  Client   │────▶│  FastAPI     │────▶│   Business Logic Layer   │   │
│   │ (Browser/ │     │  API Server  │     │                          │   │
│   │  CLI)     │     │  :8000       │     │  ┌────────┐ ┌────────┐  │   │
│   └──────────┘     └──────────────┘     │  │Services│ │ Models │  │   │
│                           │              │  └───┬────┘ └───┬────┘  │   │
│                           │              └──────┼──────────┼───────┘   │
│                           │                     │          │           │
│                    ┌──────▼──────┐        ┌─────▼────┐ ┌──▼────────┐  │
│                    │  Swagger UI │        │PostgreSQL│ │   Redis    │  │
│                    │  /docs      │        │  :5432   │ │   :6379   │  │
│                    └─────────────┘        └──────────┘ └───────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites Checklist

> **Before you begin**, ensure you have the following tools installed on your machine.

| | Tool | Required Version | Purpose | Required? |
|:---:|:---|:---:|:---|:---:|
| ![Python](https://img.shields.io/badge/-Python-3776AB?style=flat-square&logo=python&logoColor=white) | **Python** | ![v3.11+](https://img.shields.io/badge/v3.11+-blue?style=flat-square) | Runtime environment | **Required** |
| ![Docker](https://img.shields.io/badge/-Docker-2496ED?style=flat-square&logo=docker&logoColor=white) | **Docker & Docker Compose** | ![Latest](https://img.shields.io/badge/Latest-blue?style=flat-square) | Local database & cache services | **Required** |
| ![Git](https://img.shields.io/badge/-Git-F05032?style=flat-square&logo=git&logoColor=white) | **Git** | ![v2.30+](https://img.shields.io/badge/v2.30+-blue?style=flat-square) | Version control | **Required** |
| ![VS Code](https://img.shields.io/badge/-VS_Code-007ACC?style=flat-square&logo=visualstudiocode&logoColor=white) | **IDE (VS Code recommended)** | ![Latest](https://img.shields.io/badge/Latest-blue?style=flat-square) | Development environment | *Optional* |
| ![Postman](https://img.shields.io/badge/-Postman-FF6C37?style=flat-square&logo=postman&logoColor=white) | **API Client (Postman/curl)** | ![Any](https://img.shields.io/badge/Any-gray?style=flat-square) | API testing | *Optional* |

---

## Quick Start Guide

```
  ┌───────┐    ┌───────┐    ┌───────┐    ┌───────┐    ┌───────┐    ┌───────┐
  │ Step  │───▶│ Step  │───▶│ Step  │───▶│ Step  │───▶│ Step  │───▶│ Step  │
  │   1   │    │   2   │    │   3   │    │   4   │    │   5   │    │   6   │
  │ Clone │    │ Setup │    │Config │    │Docker │    │  Run  │    │Access │
  └───────┘    └───────┘    └───────┘    └───────┘    └───────┘    └───────┘
```

---

### Step 1 Clone the Repository

<table>
<tr>
<td width="80">

```
╔═══╗
║ 1 ║
╚═══╝
```

</td>
<td>

**Clone the Repository** | *Required*

Get the ECTP source code on your local machine.

```bash
git clone <repository-url>
cd ECTP
```

> **Tip:** If you don't have access to the repository, contact the Platform Architect for permissions.

</td>
</tr>
</table>

---

### Step 2 Set Up Python Environment

<table>
<tr>
<td width="80">

```
╔═══╗
║ 2 ║
╚═══╝
```

</td>
<td>

**Set Up Python Environment** | *Required*

Create an isolated Python environment and install all project dependencies.

```bash
# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

> **Warning:** Make sure you are using Python 3.11 or higher. Older versions are not supported and may cause compatibility issues.

</td>
</tr>
</table>

---

### Step 3 Configure Environment

<table>
<tr>
<td width="80">

```
╔═══╗
║ 3 ║
╚═══╝
```

</td>
<td>

**Configure Environment Variables** | *Required*

Set up your local environment configuration.

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your local settings
# At minimum, set:
#   ECTP_DB_PASSWORD=your_local_postgres_password
#   ECTP_REDIS_HOST=localhost
```

> **Warning:** Never commit your `.env` file to version control. It is already listed in `.gitignore`.

</td>
</tr>
</table>

---

### Step 4 Start Local Services (Docker)

<table>
<tr>
<td width="80">

```
╔═══╗
║ 4 ║
╚═══╝
```

</td>
<td>

**Start Local Services** | *Required*

Launch PostgreSQL and Redis using Docker Compose.

```bash
# Start PostgreSQL and Redis
docker-compose -f deployment/docker/docker-compose.yml up -d
```

> **Tip:** Run `docker-compose ps` to verify both services are running and healthy.

</td>
</tr>
</table>

---

### Step 5 Run the Application

<table>
<tr>
<td width="80">

```
╔═══╗
║ 5 ║
╚═══╝
```

</td>
<td>

**Start the API Server** | *Required*

Launch the ECTP FastAPI application with hot reload enabled.

```bash
# Start the API server
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

> **Tip:** The `--reload` flag enables auto-restart on code changes, ideal for development.

</td>
</tr>
</table>

---

### Step 6 Access the API

<table>
<tr>
<td width="80">

```
╔═══╗
║ 6 ║
╚═══╝
```

</td>
<td>

**Access the API Endpoints** | *Required*

Verify the application is running by visiting the following URLs:

| Endpoint | URL | Description |
|:---|:---|:---|
| **Swagger UI** | [http://localhost:8000/docs](http://localhost:8000/docs) | Interactive API documentation |
| **ReDoc** | [http://localhost:8000/redoc](http://localhost:8000/redoc) | Alternative API documentation |
| **Health Check** | [http://localhost:8000/health](http://localhost:8000/health) | Application health status |

> **Success!** If the health check returns a `200 OK` response, your setup is complete!

</td>
</tr>
</table>

---

## What's Next?

<table>
<tr>
<td width="50%" valign="top">

### Architecture & Design

```
┌──────────────────────────┐
│  Architecture Document   │
│                          │
│  Understand the system   │
│  design, components,     │
│  and data flow.          │
│                          │
│  >> Deep dive into the   │
│     platform blueprint   │
└──────────────────────────┘
```

[Read the Architecture Document](../architecture/architecture-document.md)

</td>
<td width="50%" valign="top">

### API Standards

```
┌──────────────────────────┐
│  API Design Standards    │
│                          │
│  Learn about REST        │
│  conventions, naming,    │
│  and error handling.     │
│                          │
│  >> Build consistent     │
│     and clean APIs       │
└──────────────────────────┘
```

[Review API Design Standards](../design/api-design-standards.md)

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Contributing

```
┌──────────────────────────┐
│  Contributing Guide      │
│                          │
│  Understand branching    │
│  strategy, code review   │
│  process, and CI/CD.     │
│                          │
│  >> Start contributing   │
│     to ECTP today        │
└──────────────────────────┘
```

[Check the Contributing Guide](../../CONTRIBUTING.md)

</td>
<td width="50%" valign="top">

### Operations

```
┌──────────────────────────┐
│  Admin Runbook           │
│                          │
│  Operational procedures, │
│  incident response, and  │
│  maintenance guides.     │
│                          │
│  >> Master platform      │
│     operations           │
└──────────────────────────┘
```

[Explore the Admin Runbook](../runbooks/admin-runbook.md)

</td>
</tr>
</table>

---

## Getting Help

```
╔══════════════════════════════════════════════════════════════════════╗
║                         Need Assistance?                            ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║   Documentation .......... Check documentation in /docs              ║
║   ADRs ................... Review Architecture Decision Records      ║
║                            in /docs/adr                              ║
║   Contact ................ Gopi Krishna Vajrala                       ║
║                            (Platform Architect)                      ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

<div align="center">

**Author:** Gopi Krishna Vajrala

*Happy building! Welcome to the ECTP team.*

</div>
