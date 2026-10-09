"""T3-3 — expurgo verificável por Relation fora do Work (C12/T3-3).

Cobertura da auditoria (docs/auditoria_c12_fechamento.md, T3-3): só
existia expurgo do domínio Work (``work/retention.py``) e deleção por
usuário (``core/db/users.py``); as colunas de Relation já migraram para
famílias de tabela fora do Work, mas faltava rotina ``purge``/``verify``
/auditoria espelhando o expurgo do Work.

Regras espelhadas de ``work/retention.py``:
- endereçar **o que está carimbado** (``relation_id = ?``): NULL nunca é
  alcançado sem inferência (fail-closed);
- redigir campo a campo com substituto NOT NULL-aware (``''``/``NULL``);
- verificação campo a campo (sobreviventes derrubam o resultado);
- evento de auditoria imune a expurgos futuros (tabela fora do mapa);
- nenhuma apagação por ``user_id`` — só Relation explícita.
"""
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub


def _schema_db():
    from core.db.relations import RelationsDatabaseMixin
    from core.db.schema import SchemaDatabaseMixin
    from engines.will_scope import WillScopeDatabaseMixin
    from work.tenancy import WorkTenancyDatabaseMixin

    class _RealSchema(
        RelationsDatabaseMixin,
        WillScopeDatabaseMixin,
        SchemaDatabaseMixin,
        WorkTenancyDatabaseMixin,
    ):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()
            self._init_sqlite_schema()

    return _RealSchema()


def _seed(db, *, relation_id, user_id="u1", titulo="obra"):
    """Semeia conteudo representativo em tres familias fora do Work."""
    db.conn.execute(
        "INSERT INTO conversations (user_id, user_name, user_input, ai_response,"
        " relation_id, agent_instance) VALUES (?, 'N', ?, ?, ?, 'jung_v1')",
        (user_id, f"entrada {titulo}", f"resposta {titulo}", relation_id),
    )
    db.conn.execute(
        "INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value,"
        " relation_id, agent_instance) VALUES (?, 'pref', 'k', ?, ?, 'jung_v1')",
        (user_id, f"fato {titulo}", relation_id),
    )
    db.conn.execute(
        "INSERT INTO agent_hobby_artifacts (user_id, title, summary,"
        " relation_id, agent_instance, scope_kind) VALUES (?, ?, ?, ?, 'jung_v1', 'relation')",
        (user_id, titulo, f"resumo {titulo}", relation_id),
    )
    db.conn.commit()


def test_expurga_relation_e_preserva_as_demas_e_a_quarentena():
    """So o que esta carimbado com a Relation alvo e redigido: a Relation
    vizinha e a quarentena legada (NULL) ficam intactas — o enderecamento
    e `relation_id = ?`, nunca inferencia por user_id."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    _seed(db, relation_id="rel-A", titulo="A")
    _seed(db, relation_id="rel-B", titulo="B")
    _seed(db, relation_id=None, titulo="legado")

    counts = erase_relation(db.conn, "rel-A")
    assert counts.get("conversations", 0) >= 2, "user_input + ai_response"
    assert counts.get("user_facts", 0) >= 1
    assert counts.get("agent_hobby_artifacts", 0) >= 2, "title + summary"

    assert verify_relation_erase(db.conn, "rel-A") == {}, "A fica limpa"

    row_b = db.conn.execute(
        "SELECT user_input, ai_response FROM conversations WHERE relation_id = 'rel-B'"
    ).fetchone()
    assert "entrada B" in row_b["user_input"], "Relation vizinha intacta"
    row_null = db.conn.execute(
        "SELECT user_input FROM conversations WHERE relation_id IS NULL"
    ).fetchone()
    assert "entrada legado" in row_null["user_input"], (
        "quarentena NULL nao e alcançavel sem inferencia (fail-closed)"
    )
    assert verify_relation_erase(db.conn, "rel-B") != {}, (
        "a vizinha tem conteudo (reporta sobreviventes, nao apagados)"
    )


def test_nao_excede_os_limites_do_user_id():
    """Mesmo user_id em duas Relations: apagar rel-A nao toca rel-B e
    deletar por user_id nunca entra no caminho do expurgo."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    _seed(db, relation_id="rel-A", user_id="mesmo-user", titulo="A")
    _seed(db, relation_id="rel-B", user_id="mesmo-user", titulo="B")

    erase_relation(db.conn, "rel-A")
    surviving = verify_relation_erase(db.conn, "rel-B")
    assert surviving, "rel-B segue com conteudo proprio"
    intact = db.conn.execute(
        "SELECT user_input FROM conversations"
        " WHERE relation_id = 'rel-B' AND user_id = 'mesmo-user'"
    ).fetchone()
    assert "entrada B" in intact["user_input"]


def test_colunas_not_null_sao_redigidas_para_string_vazia():
    """user_facts.fact_value e NOT NULL: substituto NULL daria
    IntegrityError (P1 do PR #48 no Work) — espelha _replacement_for."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    _seed(db, relation_id="rel-A")
    counts = erase_relation(db.conn, "rel-A")
    assert counts["user_facts"] >= 3, "fact_category/fact_key/fact_value"
    fact = db.conn.execute(
        "SELECT fact_value, fact_key FROM user_facts WHERE relation_id = 'rel-A'"
    ).fetchone()
    assert fact["fact_value"] == "" and fact["fact_key"] == ""
    assert verify_relation_erase(db.conn, "rel-A") == {}


def test_auditoria_registrada_e_imune_a_expurgos_futuros():
    """Cada expurgo grava um evento em relation_erase_events — a tabela
    fica FORA do mapa, entao expurgos posteriores nunca a tocam (mesma
    imunidade do evento do Work)."""
    from core.db.erase import erase_relation

    db = _schema_db()
    _seed(db, relation_id="rel-A")
    erase_relation(db.conn, "rel-A")
    erase_relation(db.conn, "rel-A")  # idempotente: sem conteudo a redigir

    rows = db.conn.execute(
        "SELECT relation_id, event_key, counts_json FROM relation_erase_events"
        " WHERE relation_id = 'rel-A' ORDER BY id"
    ).fetchall()
    assert len(rows) == 2, "um evento por execucao do expurgo"
    assert rows[0]["event_key"].startswith("erase_relation:rel-A:")
    assert "conversations" in rows[0]["counts_json"]
    assert rows[1]["counts_json"] == "{}", "segunda execucao: nada a redigir"
    assert db.conn.execute(
        "SELECT COUNT(*) FROM relation_erase_events WHERE relation_id = 'rel-A'"
    ).fetchone()[0] == 2, "auditoria sobrevive aos proprios expurgos"


def test_exige_relation_e_pula_tabelas_ausentes():
    """relation_id obrigatorio (sem inferencia); tabelas fora do init
    padrao (rumination_*, relational_state) sao ignoradas com o schema
    que nao as criou — pragma-aware como o espelho."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    with pytest.raises(ValueError, match="relation_id"):
        erase_relation(db.conn, "   ")
    with pytest.raises(ValueError, match="relation_id"):
        verify_relation_erase(db.conn, "")

    _seed(db, relation_id="rel-A")
    counts = erase_relation(db.conn, "rel-A")
    assert "rumination_fragments" not in counts, "tabela inexistente pulada"
    assert "relational_state" not in counts, "tabela inexistente pulada"
