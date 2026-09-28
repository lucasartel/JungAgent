"""Particao cognitiva da camada IRT/TRI (C12c3).

As tabelas IRT carregam ``agent_instance`` (ver migrations/irt_schema.sql e a
adaptação em migrations/irt_migration.py). Acessos por usuário filtram a
partição; dados legados sem instância (``NULL``) permanecem visíveis — mesma
regra de compat do schema C12c3. Agregados de pesquisa continuam sem
identificáveis por usuário e podem ser particionados opcionalmente.
"""
from typing import Optional


def resolve_irt_instance(agent_instance: Optional[str] = None) -> Optional[str]:
    """Instância cognitiva alvo (default: instância configurada do agente)."""
    if agent_instance:
        return agent_instance
    try:
        from instance_config import AGENT_INSTANCE

        return AGENT_INSTANCE
    except ImportError:
        return None


def instance_scope_sql(alias: str = "", param_index: int = 1) -> str:
    """Cláusula ``AND`` de particao por instancia (estilo ``$n`` do Postgres)."""
    column = f"{alias}.agent_instance" if alias else "agent_instance"
    return f" AND ({column} = ${param_index} OR {column} IS NULL)"
