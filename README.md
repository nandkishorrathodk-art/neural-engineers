# 🏢 ANCOS: Autonomous Neural Company Operating System
## A Self-Managing, Self-Expanding, Multi-Agent Engineering Organization

[![Architecture](https://img.shields.io/badge/Architecture-Autonomous_Enterprise-blue.svg)](docs/ARCHITECTURE.md)
[![PRD](https://img.shields.io/badge/PRD-1.0.0-emerald.svg)](PRD.md)
[![License](https://img.shields.io/badge/License-Apache_2.0-purple.svg)](LICENSE)

ANCOS is a persistent, intelligent, multi-agent AI engineering organization capable of receiving complex real-world objectives, planning projects, creating specialized AI employees, coordinating execution, verifying results, learning from failures, and improving its own capabilities under controlled permissions.

---

## 🏛️ System Core Architecture

```
                                  ┌────────────────────────┐
                                  │   Human Owner / Board   │
                                  └───────────┬────────────┘
                                              │ Objectives & Approvals
                                              ▼
                                  ┌────────────────────────┐
                                  │   Boss AI (CEO/Brain)  │
                                  └───────────┬────────────┘
                                              │
                      ┌───────────────────────┼───────────────────────┐
                      ▼                       ▼                       ▼
          ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
          │  Task DAG & Planner  │ │ Dynamic AI Workforce │ │  Digital Boardroom   │
          └──────────────────────┘ └──────────┬───────────┘ └──────────────────────┘
                                              │
                                              ▼
                                   ┌─────────────────────┐
                                   │ Isolated Docker     │
                                   │ Workstations + VNC  │
                                   └──────────┬──────────┘
                                              │
                      ┌───────────────────────┴───────────────────────┐
                      ▼                                               ▼
          ┌──────────────────────┐                        ┌──────────────────────┐
          │  Independent QA &    │                        │ Recursive Evolution  │
          │  Skill Verification  │                        │ & Continuous Learn   │
          └──────────────────────┘                        └──────────────────────┘
```

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.12+ (managed with `uv` or `pip`)
- Docker & Docker Compose
- Node.js 20+ (for Next.js Control Room Dashboard)

### 2. Environment Setup
```bash
cp .env.example .env
```
Fill in your LLM API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) in `.env`.

### 3. Running Backend Services
```bash
# Run FastAPI Core API
python -m uvicorn neural.api.main:app --port 12000 --reload
```
API docs available at: `http://localhost:12000/docs`

### 4. Running Control Room Dashboard
```bash
cd apps/dashboard
npm install
npm run dev
```
Dashboard available at: `http://localhost:12001`

### 5. Running with Docker Compose
```bash
docker compose up --build
```

---

## 📚 Key Documentation

- [**GOAL.md**](GOAL.md) — Core vision, operational mandate, and non-negotiables
- [**CONTEXT.md**](CONTEXT.md) — Domain blueprint, agent lifecycle, and threat model
- [**PRD.md**](PRD.md) — Complete Product Requirements Document
- [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md) — 17-module deep-dive technical architecture
- [**configs/**](configs/) — Policies, resource limits, model routing, and role templates

