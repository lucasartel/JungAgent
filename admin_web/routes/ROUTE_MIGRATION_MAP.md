# Fase 0.8 - Admin Route Migration Map

Baseline generated from decorator-level AST inventory. This is the guardrail for
splitting `admin_web/routes.py` without changing the exposed route surface.

## Current Inventory

- Total admin routes declared by decorators: 112
- Legacy monolith routes in `admin_web/routes.py`: 39
- Already modular routes in `admin_web/routes/`: 73
- Snapshot fixture: `tests/fixtures/admin_route_inventory.json`
- Guardrail test: `tests/test_admin_route_inventory.py`

## Legacy Buckets

| Bucket | Routes | Intended module |
|---|---:|---|
| `legacy_research_lab` | 15 | `admin_web/routes/research_lab_routes.py` |
| `legacy_admin_core` | 13 | `admin_web/routes/admin_core_routes.py` |
| `legacy_diagnostics` | 4 | `admin_web/routes/diagnostics_routes.py` |
| `legacy_psychometrics_reports` | 4 | existing or new psychometrics/report route module |
| `legacy_user_analysis` | 3 | existing dashboard/user-analysis module or new user_analysis module |

## Existing Modular Buckets

| Module | Routes |
|---|---:|
| `work_routes.py` | 12 |
| `agent_identity_routes.py` | 11 |
| `irt_routes.py` | 9 |
| `admin_user_routes.py` | 6 |
| `consciousness_loop_routes.py` | 6 |
| `organization_routes.py` | 6 |
| `trigger_routes.py` | 6 |
| `auth_routes.py` | 4 |
| `dashboard_routes.py` | 4 |
| `unesco_export_routes.py` | 2 |
| `world_consciousness_routes.py` | 4 |
| `art_routes.py` | 3 |

## Recommended Cut Order

1. DONE: Extract `legacy_unesco_export` into `admin_web/routes/unesco_export_routes.py`.
2. Extract `legacy_diagnostics`. It is API-only and easy to route-compare.
3. Extract `legacy_user_analysis` and `legacy_psychometrics_reports`, keeping report generation behavior unchanged.
4. Extract `legacy_research_lab`, the largest user-facing legacy cluster.
5. Extract `legacy_admin_core` last, because it contains root/admin utility routes and instance setup behavior.

Each cut should update the route snapshot only when the route list intentionally
moves modules while preserving method/path/endpoint behavior.
