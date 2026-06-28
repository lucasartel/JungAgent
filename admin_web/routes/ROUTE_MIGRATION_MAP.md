# Fase 0.8 - Admin Route Migration Map

Baseline generated from decorator-level AST inventory. This is the guardrail for
splitting `admin_web/routes.py` without changing the exposed route surface.

## Current Inventory

- Total admin routes declared by decorators: 112
- Legacy monolith routes in `admin_web/routes.py`: 0
- Already modular routes in `admin_web/routes/`: 112
- Snapshot fixture: `tests/fixtures/admin_route_inventory.json`
- Guardrail test: `tests/test_admin_route_inventory.py`

## Legacy Buckets

All legacy route buckets have been extracted. `admin_web/routes.py` remains as
a compatibility facade without route decorators.

## Existing Modular Buckets

| Module | Routes |
|---|---:|
| `admin_core_routes.py` | 13 |
| `work_routes.py` | 12 |
| `agent_identity_routes.py` | 11 |
| `research_lab_routes.py` | 15 |
| `irt_routes.py` | 9 |
| `admin_user_routes.py` | 6 |
| `consciousness_loop_routes.py` | 6 |
| `organization_routes.py` | 6 |
| `trigger_routes.py` | 6 |
| `psychometrics_routes.py` | 4 |
| `user_analysis_routes.py` | 3 |
| `auth_routes.py` | 4 |
| `dashboard_routes.py` | 4 |
| `diagnostics_routes.py` | 4 |
| `unesco_export_routes.py` | 2 |
| `world_consciousness_routes.py` | 4 |
| `art_routes.py` | 3 |

## Recommended Cut Order

1. DONE: Extract `legacy_unesco_export` into `admin_web/routes/unesco_export_routes.py`.
2. DONE: Extract `legacy_diagnostics` into `admin_web/routes/diagnostics_routes.py`.
3. DONE: Extract `legacy_user_analysis` into `admin_web/routes/user_analysis_routes.py`.
4. DONE: Extract `legacy_psychometrics_reports` into `admin_web/routes/psychometrics_routes.py`.
5. DONE: Extract `legacy_research_lab` into `admin_web/routes/research_lab_routes.py`.
6. DONE: Extract `legacy_admin_core` into `admin_web/routes/admin_core_routes.py`.

Each cut should update the route snapshot only when the route list intentionally
moves modules while preserving method/path/endpoint behavior.
