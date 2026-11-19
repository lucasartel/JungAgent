"""R1c-1 — fila de indexação idempotente e recuperação de unidades.

Contrato do primeiro subcorte do R1c: projeção ``knowledge_unit_index``
indexada por fila durável idempotente (dedupe por unidade+versão, backoff,
recuperação de lote parcial, backfill anterior ao corte); busca além da
recência com fonte e versão corretas; escopo C12 fail-closed; derivado que
nunca sobrevive a saída de ``active`` nem ao expurgo da Relation.

Offline: SQLite temporário, sem LLM, Qdrant, rede ou produção.
"""
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`);
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.database import HybridDatabaseManager  # noqa: E402
from core.db import knowledge_unit_index as kui  # noqa: E402
from core.db.erase import erase_relation, verify_relation_erase  # noqa: E402

_INSTANCE = "jung_v1"

# Conteudos com termos distintos: "hermeneutica" so na unidade de alice,
# "declarativa" em alice/bob/global/quarentena (so o escopo separa) e
# "aplicacao" apenas na revisao que supera a unidade antiga.
_CONTENT_ALICE = ("Distincao hermeneutica sobre memoria declarativa altera "
                  "escolhas sem virar atributo nuclear.")
_CONTENT_ALICE_B = ("Caso exemplar de recuperacao tardia sem nenhuma mencao "
                    "previa em conversa sobre o assunto.")
_CONTENT_BOB = ("Memoria declarativa privada de bob explica porque a decisao "
                "dele muda diante de uma objecao sustentada.")
_CONTENT_GLOBAL = ("Tese sobre memoria declarativa sustenta recuperacao alem "
                   "da recencia recente da conversa.")
_CONTENT_QUARANTINE = ("Rumor sem fonte publica sobre memoria declarativa "
                       "fica em quarentena ilegivel para qualquer leitor.")
_CONTENT_V2 = ("Revisao da distincao: aplicacao em pergunta nova muda a "
               "resposta sustentada do agente.")
_CONTENT_ALICE_B = (
    "Caso exemplar de recuperacao tardia sem nenhuma mencao previa "
    "em conversa sobre o assunto."
)
_CONTENT_BOB = (
    "Memoria declarativa privada de bob explica porque a decisao dele "
    "muda diante de uma objecao sustentada."
)
_CONTENT_GLOBAL = (
    "Tese sobre memoria declarativa sustenta recuperacao alem da "
    "recencia recente da conversa."
)
_CONTENT_QUARANTINE = (
    "Rumor sem fonte publica sobre memoria declarativa fica em "
    "quarentena ilegivel para qualquer leitor."
)
_CONTENT_V2 = (
    "Revisao da distincao: aplicacao em pergunta nova muda a resposta "
    "sustentada do agente."
)


def _db(tmp_path, name="units.db"):
    """Banco real: schema completo, mixins reais (Relations, gaps, unidades)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _register(db, participant="alice"):
    return db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id=participant,
        status="active", consent_status="granted")


def _write(db, rel, **overrides):
    kwargs = dict(content=_CONTENT_ALICE, unit_kind="distinction",
                  epistemic_status="source_claim", source_kind="conversation",
                  source_refs=["conversation#5"], relation_id=rel)
    kwargs.update(overrides)
    return db.consolidate_knowledge_unit(**kwargs)


def _write_global(db, **overrides):
    kwargs = dict(content=_CONTENT_GLOBAL, unit_kind="concept",
                  epistemic_status="source_claim", source_kind="work_reading",
                  source_refs=["work_run#12"], ownership="global")
    kwargs.update(overrides)
    return db.consolidate_knowledge_unit(**kwargs)


def _projection_ids(db):
    rows = db.conn.execute("SELECT id FROM knowledge_unit_index")
    return {int(row["id"]) for row in rows}


def _projection_count(db):
    return db.conn.execute("SELECT COUNT(*) FROM knowledge_unit_index").fetchone()[0]


def _queue_rows(db):
    rows = db.conn.execute(
        "SELECT * FROM processing_queue WHERE queue_kind = ?",
        (kui.QUEUE_KNOWLEDGE_UNIT_INDEX,))
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Indexacao na escrita + recuperacao alem da recencia
# ---------------------------------------------------------------------------

def test_write_indexes_now_and_search_recovers_beyond_recency(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    result = _write(db, rel)

    # Projecao materializada no proprio momento da escrita (dreno imediato).
    row = db.conn.execute(
        "SELECT * FROM knowledge_unit_index WHERE id = ?",
        (result["unit_id"],),
    ).fetchone()
    assert row is not None
    # Normalizacao da projecao: caixa baixa e sem acentos.
    assert "hermeneutica" in row["search_text"]
    assert "hermenêutica" not in row["search_text"]
    assert row["origin_relation_id"] == rel
    assert row["content_version"] == 1

    # Fila duravel: um item, unidade+versao, concluido.
    items = _queue_rows(db)
    assert [item["status"] for item in items] == ["done"]
    assert items[0]["dedupe_key"] == f"{result['unit_id']}:1"

    # Fora da janela de recencia: unidade gravada ha 400 dias e recuperada
    # sem nenhuma mencao previa em conversa.
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    db.conn.execute(
        "UPDATE knowledge_units SET created_at = ?, updated_at = ? WHERE id = ?",
        (old, old, result["unit_id"]),
    )
    db.conn.commit()

    hits = db.search_knowledge_units(query="Hermenêutica da decisão", relation_id=rel)
    assert [hit["id"] for hit in hits] == [result["unit_id"]]
    hit = hits[0]
    assert hit["content"] == _CONTENT_ALICE  # a canonica manda no conteudo
    assert hit["content_version"] == 1
    assert hit["source_refs"] == ["conversation#5"]
    assert "hermeneutica" in hit["matched_terms"]
    assert hit["score"] >= 1


def test_indexing_is_idempotent_and_reprocessing_duplicates_nothing(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _write(db, rel)["unit_id"]

    # Rodada extra: nada pendente, nada regravado, nenhuma linha nova.
    round_again = db.run_knowledge_unit_index_drain()
    assert round_again["enqueued"] == 0
    assert round_again["processed"] == 0
    assert _projection_count(db) == 1

    # Dedupe da fila: unidade + versao nunca viram segundo item.
    assert db.enqueue_knowledge_unit_index(unit_id) is None
    assert db.enqueue_knowledge_unit_index(unit_id) is None
    assert len(_queue_rows(db)) == 1

    # Reaplicar o handler e upsert: continua uma linha so.
    item = {"payload": {"unit_id": unit_id, "content_version": 1},
            "agent_instance": _INSTANCE}
    kui.apply_unit_index(db, item)
    kui.apply_unit_index(db, item)
    assert _projection_count(db) == 1

    # Reforco da mesma unidade tambem nao duplica projecao nem item.
    assert _write(db, rel)["reinforced"] is True
    assert _projection_count(db) == 1
    assert len(_queue_rows(db)) == 1


# ---------------------------------------------------------------------------
# Falha parcial e recuperacao (fila com backoff)
# ---------------------------------------------------------------------------

def test_partial_failure_backs_off_and_recovers_on_next_round(
    tmp_path, monkeypatch
):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_a = _write(db, rel, content=_CONTENT_ALICE)["unit_id"]
    unit_b = _write(db, rel, content=_CONTENT_ALICE_B)["unit_id"]

    # Estado "unidades existem, indexacao nunca rodou" (banco anterior ao
    # corte): fila e projecao zeradas, so a canonica fica de pé.
    db.conn.execute("DELETE FROM knowledge_unit_index")
    db.conn.execute(
        "DELETE FROM processing_queue WHERE queue_kind = ?",
        (kui.QUEUE_KNOWLEDGE_UNIT_INDEX,),
    )
    db.conn.commit()

    original = kui._write_projection
    armed = {"unit": unit_b}

    def flaky(target_db, row):
        if armed and int(row["id"]) == armed["unit"]:
            armed.clear()
            raise RuntimeError("falha simulada de projecao")
        return original(target_db, row)

    monkeypatch.setattr(kui, "_write_projection", flaky)

    t0 = datetime.now(timezone.utc)
    first = db.run_knowledge_unit_index_drain(now=t0)
    assert first["enqueued"] == 2
    assert first["done"] == 1
    assert first["failed"] == 1
    assert _projection_ids(db) == {unit_a}
    failed_row = [
        item for item in _queue_rows(db) if item["status"] == "failed"
    ][0]
    assert "falha simulada" in failed_row["last_error"]

    # Backoff: a falha nao e reexecutada antes do prazo.
    second = db.run_knowledge_unit_index_drain(now=t0 + timedelta(minutes=1))
    assert second["enqueued"] == 0
    assert second["processed"] == 0
    assert _projection_ids(db) == {unit_a}

    # Recuperacao: apos o backoff o item volta e o lote fica completo.
    third = db.run_knowledge_unit_index_drain(now=t0 + timedelta(minutes=6))
    assert third["done"] == 1
    assert _projection_ids(db) == {unit_a, unit_b}

    stats = db.knowledge_unit_index_stats()
    assert stats["projected"] == 2
    assert stats.get("failed", 0) == 0
    assert stats.get("pending", 0) == 0
    assert stats["done"] == 2


def test_backfill_covers_units_written_before_the_cut(tmp_path, monkeypatch):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    with monkeypatch.context() as patched:
        # Escritas sem indexar: simulam as unidades anteriores a este corte.
        patched.setattr(
            kui, "refresh_unit_index", lambda *args, **kwargs: {"indexed": False}
        )
        first = _write(db, rel, content=_CONTENT_ALICE)["unit_id"]
        second = _write(db, rel, content=_CONTENT_ALICE_B)["unit_id"]
    assert _projection_count(db) == 0
    assert _queue_rows(db) == []

    round_one = db.run_knowledge_unit_index_drain()
    assert round_one["enqueued"] == 2
    assert round_one["done"] == 2
    assert _projection_ids(db) == {first, second}

    # Segunda rodada nao refaz nada (idempotente).
    round_two = db.run_knowledge_unit_index_drain()
    assert round_two["enqueued"] == 0
    assert round_two["processed"] == 0
    assert _projection_count(db) == 2


# ---------------------------------------------------------------------------
# Escopo C12 fail-closed na leitura
# ---------------------------------------------------------------------------

def test_search_scope_is_fail_closed(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice")
    rel_b = _register(db, "bob")
    id_a = _write(db, rel_a, content=_CONTENT_ALICE)["unit_id"]
    id_b = _write(db, rel_b, content=_CONTENT_BOB)["unit_id"]
    id_g = _write_global(db)["unit_id"]
    id_q = _write(
        db,
        rel_a,
        content=_CONTENT_QUARANTINE,
        ownership="global",
        relation_id=None,
        source_kind="conversation",
        source_refs=["conversation#9"],
    )["unit_id"]

    # Todos os quatro conteudos trazem "declarativa": so o escopo separa.
    query = "memoria declarativa"
    alice_hits = {h["id"] for h in
                  db.search_knowledge_units(query=query, relation_id=rel_a)}
    assert alice_hits == {id_a, id_g}

    bob_hits = {h["id"] for h in
                db.search_knowledge_units(query=query, relation_id=rel_b)}
    assert bob_hits == {id_b, id_g}

    # Sem Relation: somente o material global admitido, nunca privado.
    global_hits = {h["id"] for h in db.search_knowledge_units(query=query)}
    assert global_hits == {id_g}

    # Quarentena F-5: linha existe para auditoria, mas ninguem a serve.
    assert id_q not in alice_hits | bob_hits | global_hits

    # Relation revogada: falha fechada ANTES de qualquer leitura.
    db.conn.execute(
        "UPDATE agent_relations SET status = 'revoked', consent_status = 'revoked' "
        "WHERE relation_id = ?",
        (rel_a,),
    )
    db.conn.commit()
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db.search_knowledge_units(query=query, relation_id=rel_a)
    # A revogacao nao apaga nada: linha canonica e projecao permanecem.
    assert _projection_count(db) == 3


# ---------------------------------------------------------------------------
# Derivado acompanha a transicao de status e o expurgo
# ---------------------------------------------------------------------------

def test_status_transitions_remove_the_derived_row_immediately(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _write(db, rel)["unit_id"]
    assert _projection_count(db) == 1

    # hold -> under_review: derivado sai na hora, sem esperar dreno.
    db.review_knowledge_unit(
        unit_id, decision="hold", reviewer="lucas", reason="revisao pendente"
    )
    assert _projection_count(db) == 0
    assert db.search_knowledge_units(query="hermeneutica", relation_id=rel) == []

    # validate -> active: item idempotente e a unidade volta a ser recuperavel.
    db.review_knowledge_unit(
        unit_id, decision="validate", reviewer="lucas", reason="fonte conferida"
    )
    assert _projection_count(db) == 1
    hits = db.search_knowledge_units(query="hermeneutica", relation_id=rel)
    assert [hit["id"] for hit in hits] == [unit_id]

    # retract -> terminal: derivado sai e a unidade some da busca.
    db.review_knowledge_unit(unit_id, decision="retract", reviewer="lucas",
                             reason="correcao sustentada")
    assert _projection_count(db) == 0
    assert db.search_knowledge_units(query="hermeneutica", relation_id=rel) == []


def test_supersede_moves_the_projection_to_the_new_version(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    old = _write(db, rel, content=_CONTENT_ALICE)["unit_id"]
    new = db.consolidate_knowledge_unit(
        content=_CONTENT_V2,
        unit_kind="distinction",
        epistemic_status="source_claim",
        source_kind="conversation",
        source_refs=["conversation#7"],
        relation_id=rel,
        supersedes_unit_id=old,
    )["unit_id"]

    # A projecao nova ocupa o lugar da antiga; a linha superada fica para
    # auditoria, mas nunca volta como evidencia.
    assert _projection_ids(db) == {new}
    hits = db.search_knowledge_units(query="aplicacao", relation_id=rel)
    assert [hit["id"] for hit in hits] == [new]
    assert hits[0]["content_version"] == 2
    assert db.search_knowledge_units(query="hermeneutica", relation_id=rel) == []
    old_row = db.conn.execute(
        "SELECT status, superseded_by FROM knowledge_units WHERE id = ?", (old,)
    ).fetchone()
    assert old_row["status"] == "superseded"
    assert old_row["superseded_by"] == new


def test_relation_erase_removes_projection_and_keeps_global(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    id_a = _write(db, rel, content=_CONTENT_ALICE)["unit_id"]
    id_g = _write_global(db)["unit_id"]

    remaining = erase_relation(db.conn, rel)
    assert "knowledge_unit_index" in remaining
    assert verify_relation_erase(db.conn, rel) == {}
    assert _projection_ids(db) == {id_g}

    # Tombstone sem texto nao entra na fila: nada recria o derivado apagado.
    round_again = db.run_knowledge_unit_index_drain()
    assert round_again["enqueued"] == 0
    assert verify_relation_erase(db.conn, rel) == {}
    assert _projection_ids(db) == {id_g}
    assert id_a not in _projection_ids(db)

    # A busca global continua servindo; a da Relation nao tem o que devolver.
    hits = db.search_knowledge_units(query="declarativa", relation_id=rel)
    assert [hit["id"] for hit in hits] == [id_g]
    assert db.search_knowledge_units(query="hermeneutica", relation_id=rel) == []


# ---------------------------------------------------------------------------
# Guardas da consulta e forma do resultado
# ---------------------------------------------------------------------------

def test_writes_leave_no_open_transaction_for_other_connections(tmp_path):
    """Nenhum caminho desta fila deixa transacao aberta (lock em outra conexao).

    O sqlite3 do Python inicia transacao em QUALQUER DML, inclusive
    ``UPDATE``/``DELETE`` sem correspondencia: commitar so quando
    ``rowcount > 0`` segurava o lock de escrita da conexao e derrubava
    outro escritor com ``database is locked`` (ate o timeout). O bug vivia
    em ``processing_queue.claim_due``/``_reconcile_exhausted_running`` e
    passou a ser acionado a cada escrita de unidade por causa do dreno.
    """
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _write(db, rel)["unit_id"]

    # Segunda conexao com timeout ZERO: transacao aberta na primeira
    # conexao faz a escrita falhar na hora, sem esperar o timeout de 5s.
    other = sqlite3.connect(str(tmp_path / "units.db"), timeout=0)
    other.row_factory = sqlite3.Row

    def probe(label):
        other.execute("CREATE TABLE IF NOT EXISTS lock_probe (label TEXT)")
        other.execute("INSERT INTO lock_probe (label) VALUES (?)", (label,))
        other.commit()

    try:
        # 1) Rodada de dreno sem nada pendente: claim e reconciliacao rodam
        #    ``UPDATE``/``DELETE`` sem nenhuma linha correspondente.
        db.run_knowledge_unit_index_drain()
        probe("drain-vazio")

        # 2) Transicao que apaga projecao existente (DELETE com match).
        db.review_knowledge_unit(unit_id, decision="hold", reviewer="lucas",
                                 reason="revisao pendente")
        probe("hold")

        # 3) Transicao sem projecao para apagar (DELETE sem match).
        db.review_knowledge_unit(unit_id, decision="retract", reviewer="lucas",
                                 reason="correcao sustentada")
        probe("retract")
    finally:
        other.close()


def test_query_guards_and_result_shape(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _write(db, rel)["unit_id"]

    # Query sem termo util e recusa auditavel, nao busca vazia silenciosa.
    with pytest.raises(ValueError, match="query_required"):
        db.search_knowledge_units(query="", relation_id=rel)
    with pytest.raises(ValueError, match="query_required"):
        db.search_knowledge_units(query="  ...  ", relation_id=rel)

    # Limite cortado na faixa util (minimo 1).
    assert len(db.search_knowledge_units(query="declarativa", relation_id=rel, limit=0)) == 1

    # validated_only: so unidade promovida pela revisao registrada entra.
    assert db.search_knowledge_units(query="hermeneutica", relation_id=rel, validated_only=True) == []
    db.review_knowledge_unit(
        unit_id, decision="validate", reviewer="lucas", reason="fonte conferida"
    )
    hits = db.search_knowledge_units(query="hermeneutica", relation_id=rel, validated_only=True)
    assert [hit["id"] for hit in hits] == [unit_id]

    # Resultado carrega o que o R1c exige: fonte, versao e escopo.
    hit = db.search_knowledge_units(query="hermeneutica", relation_id=rel)[0]
    assert hit["ownership_class"] == "relation_private"
    assert hit["origin_relation_id"] == rel
    assert hit["content_version"] == 1
    assert hit["source_refs"] == ["conversation#5"]
    assert hit["epistemic_status"] == "validated_synthesis"
