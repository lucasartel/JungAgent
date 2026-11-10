"""F-5e — tratamento das leituras: material global sem autorizacao verificavel.

Aplica a regra escolhida pelo mantenedor (09/11/2026) — *evidencia por
tabela* — aos 13 sítios de leitura + ao leitor principal:

* ``authorized_aggregate`` so e servido com prova de proveniencia;
* ``instance_global`` segue servindo (escopo da instancia, nao afirmacao
  de autorizacao);
* ``origin_quarantined`` (quando existir) bloqueia qualquer classe;
* ``knowledge_gaps`` — a unica tabela com ``source_kind`` — passa pelo
  contrato REAL (``assess_global_admission``), sem duplicar regra.

**Nada aqui reclassifica historico**: a recusa e exclusao na LEITURA e a
linha permanece intacta — testado explicitamente.
"""
import json
import sqlite3
import sys
import threading

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`);
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db.cognitive_ownership import (  # noqa: E402
    globally_readable_sql,
    is_globally_readable,
)
from core.database import HybridDatabaseManager  # noqa: E402

_INSTANCE = "jung_v1"


def _db():
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(":memory:")
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _row(db, gap_id):
    return db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Condicao SQL
# ---------------------------------------------------------------------------
def test_globally_readable_sql_variants():
    # Com ancoras: a afirmacao exige prova.
    with_anchors = globally_readable_sql({"origin_class", "source_refs_json"})
    assert "origin_class <> 'authorized_aggregate'" in with_anchors
    assert "source_refs_json" in with_anchors

    # Sem coluna de ancoras: a afirmacao nao e verificavel -> nao sai.
    assert globally_readable_sql({"origin_class"}) == (
        "(origin_class <> 'authorized_aggregate')"
    )

    # Sem coluna de origem: nao ha o que verificar.
    assert globally_readable_sql({"qualquer"}) == "1 = 1"

    # Quarentena bloqueia qualquer classe.
    with_flag = globally_readable_sql(
        {"origin_class", "source_refs_json", "origin_quarantined"}
    )
    assert "origin_quarantined = 0" in with_flag

    # Alias em JOIN.
    aliased = globally_readable_sql(
        {"origin_class", "source_refs_json"}, prefix="t."
    )
    assert "t.origin_class" in aliased and "t.source_refs_json" in aliased

    # ownership_class (goal_threads).
    ownership = globally_readable_sql(
        {"ownership_class", "source_refs_json"}, origin_column="ownership_class"
    )
    assert "ownership_class <> 'authorized_aggregate'" in ownership


# ---------------------------------------------------------------------------
# Predicado REAL (knowledge_gaps — unica tabela com source_kind)
# ---------------------------------------------------------------------------
def test_predicate_real_rows():
    base = {
        "origin_class": "authorized_aggregate",
        "origin_quarantined": 0,
        "origin_relation_id": None,
        "source_kind": "world",
        "source_refs_json": '["will#30"]',
        "provenance_json": "{}",
    }
    assert is_globally_readable(base, agent_instance=_INSTANCE) is True
    assert is_globally_readable(
        dict(base, source_kind="conversation"), agent_instance=_INSTANCE
    ) is False
    assert is_globally_readable(
        dict(base, source_refs_json="[]"), agent_instance=_INSTANCE
    ) is False
    assert is_globally_readable(
        dict(base, origin_quarantined=1), agent_instance=_INSTANCE
    ) is False
    # instance_global segue servindo; material de Relation nao usa esta rota.
    assert is_globally_readable(
        {"origin_class": "instance_global"}, agent_instance=_INSTANCE
    ) is True
    assert is_globally_readable(
        dict(base, origin_relation_id="rel-1"), agent_instance=_INSTANCE
    ) is False


# ---------------------------------------------------------------------------
# Leitor principal: exclusao na leitura, sem tocar a linha
# ---------------------------------------------------------------------------
def test_unverifiable_global_is_not_served_and_row_is_untouched():
    db = _db()
    # Declarada global SEM prova — escrita feita por fora do contrato
    # (historico autodeclarado, exatamente o caso do F-5).
    db.conn.execute(
        """
        INSERT INTO knowledge_gaps (
            user_id, agent_instance, ownership_class, origin_class,
            source_refs_json, provenance_json, topic, the_gap,
            importance_score, status, source_kind, dedup_key,
            observation_count, last_observed_at, origin_quarantined
        ) VALUES ('u1', ?, 'instance_global', 'authorized_aggregate',
                  '[]', '{}', 'topico', 'pergunta privada', 0.5, 'open',
                  'world', 'chave', 1, '2026-11-01T00:00:00', 0)
        """,
        (_INSTANCE,),
    )
    db.conn.commit()
    gap_id = db.conn.execute(
        "SELECT id FROM knowledge_gaps"
    ).fetchone()["id"]

    # Nao servida...
    assert db.get_active_knowledge_gaps("u1", limit=10) == []
    # ...e a linha permanece EXATAMENTE como estava (condicao 4).
    row = _row(db, gap_id)
    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0
    assert row["source_refs_json"] == "[]"


def test_verified_global_is_served():
    db = _db()
    admitted = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.7,
        origin_class="authorized_aggregate",
        source_kind="world", source_refs=["will#30"],
    )
    served = db.get_active_knowledge_gaps("carol", limit=10)
    assert [g["id"] for g in served] == [admitted]


def test_instance_global_keeps_serving():
    """O escopo da instancia nao e afirmacao de autorizacao: continua saindo."""
    db = _db()
    db.conn.execute(
        """
        INSERT INTO knowledge_gaps (
            user_id, agent_instance, ownership_class, origin_class,
            source_refs_json, provenance_json, topic, the_gap,
            importance_score, status, source_kind, dedup_key,
            observation_count, last_observed_at, origin_quarantined
        ) VALUES ('u1', ?, 'instance_global', 'instance_global',
                  '[]', '{}', 'topico', 'item de instancia', 0.5, 'open',
                  'goal_manager', 'chave2', 1, '2026-11-01T00:00:00', 0)
        """,
        (_INSTANCE,),
    )
    db.conn.commit()

    served = db.get_active_knowledge_gaps("u1", limit=10)
    assert [g["the_gap"] for g in served] == ["item de instancia"]


def test_relation_scoped_material_still_served():
    """O ramo de Relation nao e afetado: segue governado pelo gate C12g."""
    db = _db()
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="alice",
        status="active", consent_status="granted",
    )
    gap_id = db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )
    served = db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10)
    assert [g["id"] for g in served] == [gap_id]


# ---------------------------------------------------------------------------
# Sítios de outras tabelas (regra de ancoras)
# ---------------------------------------------------------------------------
def test_ism_dream_without_anchors_is_not_served():
    from engines.integrative_self import IntegrativeSelfModel

    class _Conn:
        def __init__(self, conn):
            self.conn = conn
            self.agent_instance = _INSTANCE

    db = _db()
    insert = """
        INSERT INTO agent_dreams (
            agent_instance, user_id, ownership_class, origin_class,
            source_refs_json, provenance_json, dream_content,
            symbolic_theme, extracted_insight, created_at
        ) VALUES (?, 'u1', 'instance_global', ?, ?, '{}',
                  'conteudo', ?, ?, '2026-11-01')
    """
    db.conn.execute(
        insert,
        (_INSTANCE, "authorized_aggregate", '["dream#1"]',
         "tema com ancora", "insight"),
    )
    db.conn.execute(
        insert,
        (_INSTANCE, "authorized_aggregate", "[]",
         "tema sem ancora", "insight2"),
    )
    db.conn.commit()

    model = IntegrativeSelfModel(_Conn(db.conn), agent_instance=_INSTANCE)
    dreams = [
        c for c in model._latest_components(user_id="u1", relation_id=None)
        if c and c.get("key") == "dream"
    ]
    # Só o com prova de proveniencia passa.
    assert len(dreams) == 1
    assert "tema com ancora" in dreams[0]["title"]


# ---------------------------------------------------------------------------
# P1 (09/11/2026): campo preenchido nao e prova — a ancora precisa ser VALIDA
# ---------------------------------------------------------------------------
def _insert_global(db, the_gap, source_refs, *, importance=0.5, provenance="{}"):
    db.conn.execute(
        """
        INSERT INTO knowledge_gaps (
            user_id, agent_instance, ownership_class, origin_class,
            source_refs_json, provenance_json, topic, the_gap,
            importance_score, status, source_kind, dedup_key,
            observation_count, last_observed_at, origin_quarantined
        ) VALUES ('u1', ?, 'instance_global', 'authorized_aggregate',
                  ?, ?, 'topico', ?, ?, 'open', 'world', ?,
                  1, '2026-11-01T00:00:00', 0)
        """,
        (_INSTANCE, source_refs, provenance, the_gap, importance, the_gap),
    )
    db.conn.commit()


def test_anchor_must_be_a_valid_reference_not_just_a_filled_field():
    """`not json`, `[ ]`, `[null]`, objeto, escalar e `["garbage"]` nao
    servem de prova de autorizacao; so uma referencia `tipo#id` serve."""
    db = _db()
    aceitas = ['["will#30"]', '["conversation#1", "dream#9"]']
    recusadas = [
        "not json",      # P1 citado
        "[ ]",           # P1 citado
        "[]",
        "[null]",        # P1 citado
        "[0]",
        "{}",
        '"will#30"',     # escalar string
        "5",             # escalar numero
        "null",
        "",
        "['garbage']",   # sem formato tipo#id
    ]
    for i, refs in enumerate(recusadas):
        _insert_global(db, f"invalida-{i}", refs, importance=1.0 - i * 0.01)
    for i, refs in enumerate(aceitas):
        _insert_global(db, f"valida-{i}", refs, importance=0.1 - i * 0.01)

    served = {g["the_gap"] for g in db.get_active_knowledge_gaps("u1", limit=50)}
    assert served == {"valida-0", "valida-1"}, served


# ---------------------------------------------------------------------------
# P2 (09/11/2026): LIMIT antes da verificacao escondia o registro valido
# ---------------------------------------------------------------------------
def test_limit_does_not_hide_an_authorized_row_behind_a_refused_one():
    db = _db()
    # Recusada com MAIOR importancia (aparece primeiro no ORDER BY)...
    _insert_global(db, "recusada-primeiro", "not json", importance=9.0)
    # ...e a valida com menor importancia.
    _insert_global(db, "valida-depois", '["will#30"]', importance=1.0)

    # limit=1 devolvia vazio; agora devolve a valida.
    served = db.get_active_knowledge_gaps("u1", limit=1)
    assert [g["the_gap"] for g in served] == ["valida-depois"]
    # E o limite continua sendo respeitado.
    assert len(db.get_active_knowledge_gaps("u1", limit=2)) == 1


# ---------------------------------------------------------------------------
# P2 (09/11/2026): metadado historico invalido recusa a linha e CONTINUA
# ---------------------------------------------------------------------------
def test_invalid_historical_metadata_refuses_row_and_continues():
    db = _db()
    # Metadados invalidos: nao podem derrubar a leitura.
    _insert_global(db, "provenance-array", '["will#30"]', provenance="[]")
    _insert_global(db, "provenance-null", '["will#30"]', provenance="null")
    _insert_global(
        db, "flag-ambigua", '["will#30"]',
        provenance='{"private_derived": "false"}',
    )
    # ...e uma valida depois delas continua sendo servida.
    _insert_global(db, "valida-ultima", '["will#31"]', provenance="{}")

    served = {g["the_gap"] for g in db.get_active_knowledge_gaps("u1", limit=50)}
    # Nao lanca AttributeError/ValueError; recusa as invalidas.
    assert "provenance-array" not in served
    assert "provenance-null" not in served
    assert "flag-ambigua" not in served
    # "continua": a valida apos as invalidas chega.
    assert served == {"valida-ultima"}


# ---------------------------------------------------------------------------
# P1 (rodada 3): a regra SQL precisa aceitar EXATAMENTE a regra canonica
# ---------------------------------------------------------------------------
def test_sql_anchor_rule_matches_canonical_regex():
    """``ANCHOR_PREFIXES`` e a condicao GLOB tem de casar ``SOURCE_REF_RE``.

    Trava a divergencia entre as duas implementacoes (SQL nao pode ser mais
    fraco nem mais forte que o validador canonico).
    """
    from core.db.cognitive_ownership import ANCHOR_PREFIXES
    from core.db.cognitive_provenance import SOURCE_REF_RE

    # Os prefixos espelham EXATAMENTE a alternancia do regex:
    # ^(?:loop|conversation|...|research)#\d+$
    body = SOURCE_REF_RE.pattern
    assert body.startswith("^(?:") and body.endswith(")#\\d+$"), body
    alternates = set(body[len("^(?:"): -len(")#\\d+$")].split("|"))
    assert set(ANCHOR_PREFIXES) == alternates, (set(ANCHOR_PREFIXES), alternates)

    # Aceitacao identica: array com a ancora.
    import json as _json
    import sqlite3 as _sqlite3

    from core.db.cognitive_ownership import globally_readable_sql

    cond = globally_readable_sql({"origin_class", "source_refs_json"})
    conn = _sqlite3.connect(":memory:")

    def sql_accepts(anchor):
        query = (
            f"SELECT CASE WHEN {cond} THEN 1 ELSE 0 END "
            "FROM (SELECT 'authorized_aggregate' AS origin_class, ? AS source_refs_json)"
        )
        return bool(conn.execute(query, (_json.dumps([anchor]),)).fetchone()[0])

    anchors = [
        "#1", "invalid#1suffix", "will#30", "will#1suffix", "conversation#12",
        "will#", "will#0", "loop#7", "rumination_insight#4", "work_ticket#9",
        "hobby_artifact#2", "agent_development#5", "knowledge_gap#3", "research#8",
        "dream#1", "rumination_fragment#6", "work_run#1", "work_delivery#1",
        "xwill#1", "will#1#2", "will#abc", "WILL#1", "will #1", "  will#1",
        "will#1 ", "will#-1", "will#1.5", "meta#123",
    ]
    divergences = [
        anchor
        for anchor in anchors
        if bool(SOURCE_REF_RE.fullmatch(anchor)) != sql_accepts(anchor)
    ]
    assert divergences == [], divergences


# ---------------------------------------------------------------------------
# P2 (rodada 3): metadado malformado recusa a linha sem derrubar as demais
# ---------------------------------------------------------------------------
def test_malformed_json_refuses_row_without_breaking_the_rest():
    db = _db()
    # provenance nao-JSON numa linha autorizada...
    _insert_global(db, "prov-nao-json", '["will#30"]', provenance="not json")
    # ...e source_refs nao-JSON numa linha instance_global (o leitor faria
    # json.loads no valor original e lancaria JSONDecodeError).
    db.conn.execute(
        """
        INSERT INTO knowledge_gaps (
            user_id, agent_instance, ownership_class, origin_class,
            source_refs_json, provenance_json, topic, the_gap,
            importance_score, status, source_kind, dedup_key,
            observation_count, last_observed_at, origin_quarantined
        ) VALUES ('u1', ?, 'instance_global', 'instance_global',
                  'not json', '{}', 'topico', 'refs-invalidos', 0.9, 'open',
                  'goal_manager', 'refs-invalidos', 1, '2026-11-01T00:00:00', 0)
        """,
        (_INSTANCE,),
    )
    # ...e uma valida depois delas.
    _insert_global(db, "valida-depois", '["will#31"]', provenance="{}")
    db.conn.commit()

    served = {g["the_gap"] for g in db.get_active_knowledge_gaps("u1", limit=50)}
    assert "prov-nao-json" not in served
    assert "refs-invalidos" not in served
    assert served == {"valida-depois"}  # continua sem interromper


# ---------------------------------------------------------------------------
# P2 (rodada 3): ISM — 20 recusados a frente nao podem esconder o valido
# ---------------------------------------------------------------------------
def test_ism_skips_unlimited_refused_rows_to_find_the_authorized_one():
    from engines.integrative_self import IntegrativeSelfModel

    class _Conn:
        def __init__(self, conn):
            self.conn = conn
            self.agent_instance = _INSTANCE

    db = _db()
    insert = """
        INSERT INTO knowledge_gaps (
            user_id, agent_instance, ownership_class, origin_class,
            source_refs_json, provenance_json, topic, the_gap,
            importance_score, status, source_kind, dedup_key,
            observation_count, last_observed_at, origin_quarantined,
            resolved_at, closure_summary
        ) VALUES ('u1', ?, 'instance_global', ?, ?, '{}', 'topico', ?, 0.5,
                  'resolved', 'world', ?, 1, '2026-11-01T00:00:00', 0,
                  ?, 'fechado')
    """
    # 25 recusadas (sem ancora) e MAIS RECENTES...
    for i in range(25):
        db.conn.execute(
            insert,
            (_INSTANCE, "authorized_aggregate", "[]", f"recusada-{i}",
             f"rec-{i}", f"2026-11-05T10:{i:02d}:00"),
        )
    # ...e uma autorizada mais antiga (aparece por ultimo no ORDER BY).
    cursor = db.conn.execute(
        insert,
        (_INSTANCE, "authorized_aggregate", '["will#30"]', "autorizada",
         "auth", "2026-11-01T00:00:00"),
    )
    authorized_id = cursor.lastrowid
    db.conn.commit()

    model = IntegrativeSelfModel(_Conn(db.conn), agent_instance=_INSTANCE)
    gaps = [
        c for c in model._latest_components(user_id="u1", relation_id=None)
        if c and c.get("key") == "knowledge_gap"
    ]
    # LIMIT+ filtro atras devolveria vazio; o lote encontra a autorizada.
    assert len(gaps) == 1
    assert gaps[0]["source_ref"] == f"knowledge_gap#{authorized_id}"
    assert gaps[0]["title"] == "topico"
