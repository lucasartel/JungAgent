"""Corte de escopo por organizacao para consultas admin (C12c3).

Politica (decisao do mantenedor):
- master     : visao global (sem corte);
- org_admin  : visao restrita a propria org, via user_organization_mapping
               com status 'active' (mesma regra de ``can_access_user``);
- qualquer outro papel / admin ausente: fail-closed (``1 = 0``).

Uso em queries agregadas (SQL):
    clause, params = org_user_scope_clause(admin)
    cursor.execute(f"SELECT ... FROM t WHERE 1=1{clause}", params)
"""
from typing import Dict, Optional, Tuple


def org_user_scope_clause(
    admin: Optional[Dict],
    *,
    user_column: str = "user_id",
    table_alias: str = "",
) -> Tuple[str, Tuple]:
    """Clausula SQL que limita linhas aos usuarios da org do admin."""
    column = f"{table_alias}.{user_column}" if table_alias else user_column
    role = (admin or {}).get("role")

    if role == "master":
        return "", ()

    if role == "org_admin":
        org_id = admin.get("org_id")
        if not org_id:
            # org_admin sem org e impossivel de fatiar: recusar tudo.
            return " AND 1 = 0", ()
        return (
            f" AND {column} IN ("
            "SELECT user_id FROM user_organization_mapping "
            "WHERE org_id = ? AND status = 'active')",
            (org_id,),
        )

    # papel desconhecido ou admin ausente: fail-closed.
    return " AND 1 = 0", ()
