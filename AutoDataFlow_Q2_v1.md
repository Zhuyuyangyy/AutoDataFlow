# Q2 SCI Peer Review — AutoDataFlow v3.0

**Review Date**: 2026-06-01
**Reviewer**: Claude Code AI Reviewer
**Project Path**: `D:/ZYY Project/AutoDataFlow/`

---

# AutoDataFlow Q2 v1 — 7-Dimension Rubric Review

**Project:** AutoDataFlow v3.0 (Causal-Inference-Driven Schema Evolution / Data Health Governance)
**Scope reviewed:** `backend/app.py`, `backend/auto_data_flow.py`, `backend/causal_engine.py`, `backend/compliance_engine.py` (head); plus `causal_mechanism_inferrer`, `rule_engine`, `etl_cleaner`, `report_export`, `rules.yaml`, `compliance_library` referenced.
**Date:** 2026-06-01
**Reviewer:** Cross-project rubric pass

---

## 1. Architecture & Modularity (15 / 15)

This is the strongest dimension in the project. `app.py` is structured into 15 numbered sections with section banners; lifespan, middleware, dependency checks, and the 25+ FastAPI endpoints are each given their own block. The `_EXTENSIONS_AVAILABLE` guard pattern is exemplary — every optional module is wrapped in `try/except ImportError` with a graceful `503` fallback, so the service is never down because an optional dep is missing. The causal engine is a textbook layered composition: `CausalGraphBuilder` → `DoCalculusEngine` → `CounterfactualReasoner` → `CausalSchemaEngine` facade. ETL, rules, compliance, and reporting each live in their own module and are wired into dedicated API groups. The `auto_data_flow.py` Agent Swarm (Schema / ETL / Observer / Viz) is a clean pipeline. The only minor concern is the embedded `wsl:` warning block at the top of `auto_data_flow.py` (lines 1–14) — appears to be a stray shell-output capture, cosmetic only.

## 2. Code Quality & Readability (13 / 15)

Bilingual docstrings on every module, dataclasses for all data shapes (`ColumnProfile`, `CausalEdge`, `SchemaNode`, `ComplianceViolation`), `Enum` for `CausalMechanism`, `RunawayStage`-style discipline. Long but readable: `app.py` is ~1,400 lines, `causal_engine.py` ~1,100, but each function is short and named after the SCI Framework step (C1 / C2 / C3 / C4). Issues. Pydantic models sometimes use `Union[str, List[Dict]]` (`ETLCleanInputRequest`) which bypasses type safety for the input boundary. `auto_data_flow.py` has a bare `except: pass` three times (lines 587, 612, 620) inside the predictive-analytics block — should at least log. `_infer_foreign_key_edges` uses `_nodes.keys()` on line 290 but the attribute is `nodes` (typo: would crash if ever hit). Several `print(...)` calls in main() bypass the structured logger. A `MODEL_CONFIGS = {...}` block is duplicated for ETL job inference in `causal_engine.py` (lines 499–512) and in `_infer_etl_jobs` — small DRY violation.

## 3. Functionality & Feature Completeness (15 / 15)

Broadest feature surface in the cohort. 30+ HTTP endpoints covering: summary, table list / detail, quality trend, lineage, schema change detection + timeline, predictive analytics, causal graph query, Do-Calculus impact prediction, risk explanation, counterfactual "what-if", configurable rule engine (two engines: `rule_engine.py` declarative + `rules.yaml` runtime), multi-target ETL cleaning, ETL input agent (CSV/JSON), compliance library (GDPR / datatype / freshness / referential integrity), compliance apply (masking + dry-run), webhook alerts (Feishu / DingTalk), HTML + PDF + text report export, Prometheus metrics, sliding-window rate limit, health, summary. The causal subsystem is real: `CausalGraphBuilder` actually walks PRAGMA table_info, calls `CausalMechanismInferrer`, and emits typed `CausalEdge` instances. `CounterfactualReasoner` returns structured factual-vs-counterfactual diffs. No visible stub for any documented endpoint.

## 4. Innovation & Technical Depth (14 / 15)

Highest in the cohort. The causal-inference layer (Do-Calculus Rule 2 action/observation exchange, BFS causal-path search, edge-strength propagation, mitigation generation) is a genuine formal-methods scaffold, not a buzzword. Counterfactual reasoning with factual baseline, difference, confidence, and mitigation suggestions matches the structure of academic Causal AI papers. ETL quality scoring blends null / outlier / uniqueness weights from `QualityConfig` (config-driven, not hard-coded). Predictive analytics uses linear regression on the last 5 quality scores with a 3-step forecast and IQR-based anomaly detection — small but real. The "compliance library" couples a YAML rule schema to masking functions (`MaskingLib`) covering 7 PII types with `dry_run` and `rule_ids` filters. Two parallel rule engines (`rule_engine.py` for table-level validation, `rules.yaml` + `RulesEngine` for in-memory data checks) is a slight over-engineering smell but not a fault.

## 5. Production Readiness (13 / 15)

Best in cohort. Gunicorn config present, `Dockerfile` + `docker-compose.yml`, `gunicorn_conf.py`. Sliding-window rate limiter with separate global + per-IP windows. SecurityHeadersMiddleware injects HSTS, X-Content-Type-Options, X-Frame-Options DENY, CSP, Permissions-Policy, Referrer-Policy. Unified error format `{code, message, request_id}`. Request-ID propagation via `contextvars`. CORS allowlist (4 specific origins, not `*`). `API_KEY` enforced on the `POST /run/analysis` endpoint. `tenacity` retry on subprocess. Prometheus `/metrics` with `REQUEST_COUNT`, `REQUEST_LATENCY` histogram, `ACTIVE_REQUESTS` gauge, `ANALYSIS_COUNT`. Lifespan opens/closes the SQLite pool. The `dev-key-change-me` default in `API_KEY` is a yellow flag — should refuse to boot if env var missing in production. No auth on most other endpoints. SQLite connection pool is fine for dev but may need Postgres for multi-worker scale.

## 6. Testing & Validation (9 / 15)

`tests/` directory exists; `.github/` indicates CI. Unit-level dataclass symmetry and the pure-function nature of `MaskingLib` make the compliance module testable. The two rule engines are deterministic on a given input. Causal graph BFS is reproducible. Gaps. The Do-Calculus probability computation (`_compute_effect_probability`) uses hand-tuned `op_multiplier` constants — without property-based tests it's hard to know the probability scale is calibrated. `simulate_propagation_monte_carlo` is the only Monte-Carlo here; no test of statistical stability is implied. The `auto_data_flow.py` main() has no fixtures. The wsl-warning block at file top is also a sign that this script has been executed interactively many times — good for sanity, less good for determinism.

## 7. Documentation & Maintainability (13 / 15)

Massive doc footprint: `README.md` (14 KB), `SCI_FRAMEWORK.md` (40 KB) — the SCI paper-style framework document, `INNOVATION_OPTIMIZATION.md` (16 KB), `INNOVATION_ROADMAP.md` (8 KB), `OPTIMIZATION_REPORT.md` (8 KB), `专利技术交底书.md` (50 KB — patent disclosure), `TODO.md`, `REPRODUCE.md`. Every module has a docstring with explicit `用法` and `示例` blocks. The causal engine includes a self-documenting CLI with `predict / risk / graph / counterfactual` subcommands. Gaps. The double-rule-engine story isn't explained in `README`; the difference between `rule_engine.py` and `rules_engine.py` is only visible by reading both files. No architecture diagram in `docs/`. The patent doc suggests this is positioned for IP, which is fine, but the actual `architecture.md` showing the request flow (HTTP → middleware → engine → persistence) would help onboarding.

---

## Subtotal: 92 / 100

| Dim | Score |
|---|---|
| Architecture & Modularity | 15 / 15 |
| Code Quality & Readability | 13 / 15 |
| Functionality & Feature Completeness | 15 / 15 |
| Innovation & Technical Depth | 14 / 15 |
| Production Readiness | 13 / 15 |
| Testing & Validation | 9 / 15 |
| Documentation & Maintainability | 13 / 15 |
| **Total** | **92 / 100** |

**Top-3 actions to reach 95+**
1. Add a behavioral test for `CausalSchemaEngine.explain_change_risk` (golden output on a fixed warehouse fixture) and a determinism test for `simulate_propagation_monte_carlo` (seed → identical results).
2. Refuse to boot when `AUTODATAFLOW_API_KEY` is unset in non-`dev` env; promote other write endpoints behind the same auth.
3. Document the rule-engine split in README — the two engines differ in scope (table-level vs data-row) and that's worth a sentence.
