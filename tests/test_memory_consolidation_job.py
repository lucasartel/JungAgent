"""Consolidacao de memorias: elegibilidade da Relation ANTES de ler conversas
(P1), idempotencia/custo com marca de progresso (P2), pulo de usuarios sem
Relation elegivel e o scheduler diario do /meu_perfil."""

import asyncio
import sqlite3

import pytest

import jung_memory_consolidation as jmc


class _PoisonConn:
    """Falha se a consolidacao tocar o banco antes de validar a elegibilidade."""

    def cursor(self):
        raise AssertionError("consolidacao leu o banco antes de validar a elegibilidade")

    def execute(self, *args, **kwargs):
        raise AssertionError("consolidacao leu o banco antes de validar a elegibilidade")


class _StubRelationDB:
    """Banco falso com resolucao/gatilho de Relation e conn observavel."""

    def __init__(self, relation=None, conn=None, agent_instance="jung_test"):
        self.relation = relation
        self.conn = conn
        self.agent_instance = agent_instance
        self.anthropic_client = None

    def resolve_relation_id(self, *, agent_instance=None, participant_user_id=None, relation_id=None):
        if relation_id:
            return str(relation_id)
        return (self.relation or {}).get("relation_id")

    def get_agent_relation(self, relation_id):
        relation = self.relation or {}
        return relation if relation.get("relation_id") == str(relation_id) else None


def _relation(status, consent):
    return {
        "relation_id": "rel_1",
        "status": status,
        "consent_status": consent,
    }


def _memory_db(relation, timestamps):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE conversations (
            id TEXT, user_id TEXT, user_input TEXT, ai_response TEXT,
            timestamp TEXT, keywords TEXT,
            tension_level REAL, affective_charge REAL, existential_depth REAL,
            relation_id TEXT
        )
        """
    )
    for index, ts in enumerate(timestamps):
        conn.execute(
            "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"c{index}", "user_a", "in", "out", ts, "k", 0.0, 0.0, 0.0, "rel_1"),
        )
    conn.commit()
    return _StubRelationDB(relation, conn=conn)


# ---------------------------------------------------------------------------
# P1 — elegibilidade antes de ler/processar conversas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "relation,expected",
    [
        (None, "relation_scope_required_for_consolidation"),
        (_relation("active", "pending"), "relation_not_eligible_for_consolidation"),
        (_relation("paused", "granted"), "relation_not_eligible_for_consolidation"),
        (_relation("revoked", "granted"), "relation_not_eligible_for_consolidation"),
        (_relation("active", "revoked"), "relation_not_eligible_for_consolidation"),
        (_relation("archived", "granted"), "relation_not_eligible_for_consolidation"),
    ],
)
def test_consolidation_refuses_before_touching_db(relation, expected):
    """pending, paused e revoked recusam ANTES de qualquer leitura ou LLM."""
    db = _StubRelationDB(relation, conn=_PoisonConn())
    consolidator = jmc.MemoryConsolidator(db)

    with pytest.raises(ValueError, match=expected):
        consolidator.consolidate_user_memories("user_a")


def test_consolidation_accepts_active_granted_relation():
    consolidator = jmc.MemoryConsolidator(_StubRelationDB(_relation("active", "granted")))

    assert consolidator._resolve_relation_scope("user_a") == "rel_1"


# ---------------------------------------------------------------------------
# P2 — marca de progresso: sem conversas novas, nada de LLM repetido
# ---------------------------------------------------------------------------

def test_no_new_conversations_skips_llm(monkeypatch):
    """Janela identica a da ultima consolidacao nao refaz resumos (custo)."""
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 7)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)
    consolidator._save_progress(
        "user_a", "rel_1", max(timestamps), len(timestamps),
        ids=[f"c{i}" for i in range(len(timestamps))],
    )

    def _forbidden_cluster(self, memories):
        raise AssertionError("LLM nao deveria rodar sem conversas novas")

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _forbidden_cluster)

    consolidator.consolidate_user_memories("user_a")  # nao deve levantar


def test_progress_saved_and_second_run_is_idempotent(monkeypatch):
    """A marca de progresso e gravada e protege a re-execucao (boot/deploy)."""
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 7)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)

    cluster_calls = []

    def _no_clusters(self, memories):
        cluster_calls.append(len(memories))
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _no_clusters)

    consolidator.consolidate_user_memories("user_a")
    assert cluster_calls == [6]

    progress = consolidator._load_progress("user_a", "rel_1")
    assert progress["last_count"] == 6
    assert progress["last_max_ts"] == max(timestamps)

    # segunda execucao (ex.: novo boot 10 min depois) nao repete o LLM
    consolidator.consolidate_user_memories("user_a")
    assert cluster_calls == [6]


def test_integer_conversation_ids_do_not_repeat_consolidation(monkeypatch):
    """O schema de producao usa INTEGER PRIMARY KEY para conversations.id."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, user_input TEXT, ai_response TEXT,
            timestamp TEXT, keywords TEXT,
            tension_level REAL, affective_charge REAL, existential_depth REAL,
            relation_id TEXT
        )
        """
    )
    for day in range(1, 7):
        conn.execute(
            """INSERT INTO conversations
               (user_id, user_input, ai_response, timestamp, keywords,
                tension_level, affective_charge, existential_depth, relation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("user_a", "in", "out", f"2026-09-{day:02d}T10:00:00", "k", 0.0, 0.0, 0.0, "rel_1"),
        )
    conn.commit()
    consolidator = jmc.MemoryConsolidator(_StubRelationDB(_relation("active", "granted"), conn))
    cluster_calls = []

    def _cluster(self, memories):
        cluster_calls.append(len(memories))
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _cluster)
    consolidator.consolidate_user_memories("user_a")
    consolidator.consolidate_user_memories("user_a")

    assert cluster_calls == [6]
    assert consolidator._load_progress("user_a", "rel_1")["last_ids"] == {
        str(i) for i in range(1, 7)
    }


def test_new_conversations_reopen_the_window(monkeypatch):
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 7)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)
    consolidator._save_progress(
        "user_a", "rel_1", max(timestamps), len(timestamps),
        ids=[f"c{i}" for i in range(len(timestamps))],
    )

    db.conn.execute(
        "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c99", "user_a", "in", "out", "2026-09-09T10:00:00", "k", 0.0, 0.0, 0.0, "rel_1"),
    )
    db.conn.commit()

    cluster_calls = []

    def _cluster(self, memories):
        cluster_calls.append(len(memories))
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _cluster)

    consolidator.consolidate_user_memories("user_a")
    assert cluster_calls == [7]


def test_fewer_than_five_memories_skips_everything(monkeypatch):
    db = _memory_db(_relation("active", "granted"), ["2026-09-01T10:00:00"])
    consolidator = jmc.MemoryConsolidator(db)

    def _forbidden_cluster(self, memories):
        raise AssertionError("LLM nao deveria rodar com menos de 5 memorias")

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _forbidden_cluster)

    consolidator.consolidate_user_memories("user_a")
    assert consolidator._load_progress("user_a", "rel_1") is None


# ---------------------------------------------------------------------------
# P1 (round 2) — falha do LLM nao pode ser registrada como sucesso
# ---------------------------------------------------------------------------

def test_llm_error_raises_instead_of_generic_summary():
    class _BrokenClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("api fora do ar")

    db = _StubRelationDB(_relation("active", "granted"))
    db.anthropic_client = _BrokenClient()
    consolidator = jmc.MemoryConsolidator(db)

    with pytest.raises(jmc.LLMSummaryError, match="llm_summary_failed"):
        consolidator._generate_summary_with_llm(
            "trabalho",
            [{"timestamp": "2026-09-01T10:00:00", "user_input": "x", "ai_response": "y"}],
        )


def test_no_client_keeps_designed_generic_fallback():
    db = _StubRelationDB(_relation("active", "granted"))
    consolidator = jmc.MemoryConsolidator(db)

    summary = consolidator._generate_summary_with_llm(
        "trabalho",
        [{"timestamp": "2026-09-01T10:00:00", "user_input": "x", "ai_response": "y"}],
    )
    assert "trabalho" in summary


def test_llm_failure_is_not_recorded_as_success(monkeypatch):
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 7)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)

    def _cluster(self, memories):
        return {"trabalho": list(memories)}

    def _fail(self, **kwargs):
        raise jmc.LLMSummaryError("llm_summary_failed: boom")

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _cluster)
    monkeypatch.setattr(jmc.MemoryConsolidator, "_create_consolidated_memory", _fail)

    consolidator.consolidate_user_memories("user_a")  # nao propaga

    assert consolidator._load_progress("user_a", "rel_1") is None


def test_failed_summary_is_retried_without_new_conversations(monkeypatch):
    """Recuperacao: falha sem marca permite refazer o resumo sem entrada nova."""
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 7)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)

    def _cluster(self, memories):
        return {"trabalho": list(memories)}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _cluster)

    attempts = []

    def _ok(self, **kwargs):
        attempts.append("ok")
        return None

    def _fail(self, **kwargs):
        attempts.append("fail")
        raise jmc.LLMSummaryError("llm_summary_failed: boom")

    # 1) sucesso inicial: marca gravada
    monkeypatch.setattr(jmc.MemoryConsolidator, "_create_consolidated_memory", _ok)
    consolidator.consolidate_user_memories("user_a")
    assert consolidator._load_progress("user_a", "rel_1")["last_ids"] == set(
        f"c{i}" for i in range(6)
    )

    # 2) entrada nova + falha do LLM: a marca NAO cobre a nova entrada
    db.conn.execute(
        "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c99", "user_a", "in", "out", "2026-09-09T10:00:00", "k", 0.0, 0.0, 0.0, "rel_1"),
    )
    db.conn.commit()
    monkeypatch.setattr(jmc.MemoryConsolidator, "_create_consolidated_memory", _fail)
    consolidator.consolidate_user_memories("user_a")
    assert "c99" not in consolidator._load_progress("user_a", "rel_1")["last_ids"]

    # 3) SEM entrada nova: ainda assim o resumo e recuperado
    monkeypatch.setattr(jmc.MemoryConsolidator, "_create_consolidated_memory", _ok)
    consolidator.consolidate_user_memories("user_a")

    assert attempts == ["ok", "fail", "ok"]
    assert "c99" in consolidator._load_progress("user_a", "rel_1")["last_ids"]


def test_partial_failure_blocks_progress_mark(monkeypatch):
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 13)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)

    def _cluster(self, memories):
        return {"a": memories[:6], "b": memories[6:]}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _cluster)

    calls = []

    def _mixed(self, **kwargs):
        calls.append(kwargs["topic"])
        if kwargs["topic"] == "b":
            raise jmc.LLMSummaryError("llm_summary_failed: boom")
        return None

    monkeypatch.setattr(jmc.MemoryConsolidator, "_create_consolidated_memory", _mixed)

    consolidator.consolidate_user_memories("user_a")

    assert calls == ["a", "b"]  # um tema falhar nao interrompe os demais
    assert consolidator._load_progress("user_a", "rel_1") is None


# ---------------------------------------------------------------------------
# P2 (round 2) — saida da janela rolante nao e entrada nova
# ---------------------------------------------------------------------------

def test_rolling_window_outflow_does_not_retrigger(monkeypatch):
    """Conversa antiga saindo da janela nao deve refazer os resumos pagos."""
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 8)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)

    def _no_clusters(self, memories):
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _no_clusters)
    consolidator.consolidate_user_memories("user_a")  # grava a marca

    def _forbidden(self, memories):
        raise AssertionError("LLM nao deveria rodar: saida da janela nao e entrada nova")

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _forbidden)
    db.conn.execute("DELETE FROM conversations WHERE timestamp = ?", (timestamps[0],))
    db.conn.commit()

    consolidator.consolidate_user_memories("user_a")  # deve pular


def test_backfilled_conversation_retriggers(monkeypatch):
    """Entrada nova (mesmo antiga) aumenta a contagem e reabre o ciclo."""
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 8)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)

    def _no_clusters(self, memories):
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _no_clusters)
    consolidator.consolidate_user_memories("user_a")

    db.conn.execute(
        "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c_old", "user_a", "in", "out", "2026-08-01T10:00:00", "k", 0.0, 0.0, 0.0, "rel_1"),
    )
    db.conn.commit()

    cluster_calls = []

    def _cluster(self, memories):
        cluster_calls.append(len(memories))
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _cluster)

    consolidator.consolidate_user_memories("user_a")
    assert cluster_calls == [8]


def test_swap_with_backdated_entry_retriggers(monkeypatch):
    """Borda exata: sai uma antiga, entra uma retroativa — count e max iguais,
    mas ha entrada nova (por id) e o ciclo precisa rodar."""
    timestamps = [f"2026-09-{day:02d}T10:00:00" for day in range(1, 8)]
    db = _memory_db(_relation("active", "granted"), timestamps)
    consolidator = jmc.MemoryConsolidator(db)

    def _no_clusters(self, memories):
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _no_clusters)
    consolidator.consolidate_user_memories("user_a")  # grava a marca

    # sai a conversa mais antiga; entra uma retroativa (data anterior a max)
    db.conn.execute("DELETE FROM conversations WHERE timestamp = ?", (timestamps[0],))
    db.conn.execute(
        "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c_back", "user_a", "in", "out", "2026-08-15T10:00:00", "k", 0.0, 0.0, 0.0, "rel_1"),
    )
    db.conn.commit()

    cluster_calls = []

    def _cluster(self, memories):
        cluster_calls.append(len(memories))
        return {}

    monkeypatch.setattr(jmc.MemoryConsolidator, "_cluster_by_topic", _cluster)

    consolidator.consolidate_user_memories("user_a")
    assert cluster_calls == [7]  # contagem igual, mas entrada nova foi vista


# ---------------------------------------------------------------------------
# Bug de producao (24/09): resposta vazia do LLM (content=None) nao pode virar
# AttributeError nem resumo generico — e falha recuperavel com orcamento real.
# ---------------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, blocks):
        self.content = blocks


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _fake_memories(n=6):
    return [
        {
            "id": f"c{i}",
            "timestamp": f"2026-09-{i + 1:02d}T10:00:00",
            "user_input": "in",
            "ai_response": "out",
        }
        for i in range(n)
    ]


def test_empty_llm_content_is_failure_not_generic_summary():
    """Producao 24/09: glm-5 com orcamento curto devolve content=None."""
    db = _StubRelationDB(_relation("active", "granted"))
    client = _FakeClient(_FakeResponse([_FakeBlock(None)]))
    db.anthropic_client = client
    consolidator = jmc.MemoryConsolidator(db)

    with pytest.raises(jmc.LLMSummaryError, match="resposta vazia"):
        consolidator._generate_summary_with_llm("trabalho", _fake_memories())
    assert client.messages.calls[0]["max_tokens"] == 2000  # orcamento real


def test_llm_text_extracted_from_any_block():
    db = _StubRelationDB(_relation("active", "granted"))
    db.anthropic_client = _FakeClient(
        _FakeResponse([_FakeBlock(None), _FakeBlock("  resumo ok  ")])
    )
    consolidator = jmc.MemoryConsolidator(db)

    assert (
        consolidator._generate_summary_with_llm("trabalho", _fake_memories())
        == "resumo ok"
    )


def test_empty_content_list_is_failure():
    db = _StubRelationDB(_relation("active", "granted"))
    db.anthropic_client = _FakeClient(_FakeResponse([]))
    consolidator = jmc.MemoryConsolidator(db)

    with pytest.raises(jmc.LLMSummaryError, match="resposta vazia"):
        consolidator._generate_summary_with_llm("trabalho", _fake_memories())


# ---------------------------------------------------------------------------
# P2 (round 3) — o gate de consentimento falha FECHADO sem o resolvedor
# ---------------------------------------------------------------------------

class _NoResolverDB:
    """Banco sem API de Relations: o gate nao pode ser avaliado."""

    def __init__(self):
        self.conn = _PoisonConn()
        self.agent_instance = "jung_test"
        self.anthropic_client = None


def test_consent_gate_fails_closed_without_resolver():
    consolidator = jmc.MemoryConsolidator(_NoResolverDB())

    with pytest.raises(ValueError, match="consent_gate_unavailable_for_consolidation"):
        consolidator.consolidate_user_memories("user_a")


def test_run_consolidation_job_refuses_without_consent_resolver(monkeypatch):
    """O job inteiro e recusado antes de consultar qualquer usuario."""

    class _NeverBuilt:
        def __init__(self, db):
            raise AssertionError("nem deveria construir o consolidador")

    monkeypatch.setattr(jmc, "MemoryConsolidator", _NeverBuilt)

    class _DB:
        pass

    db = _DB()
    db.conn = sqlite3.connect(":memory:")
    db.conn.execute("CREATE TABLE conversations (user_id TEXT)")

    jmc.run_consolidation_job(db)  # recusa estrutural, sem excecao


# ---------------------------------------------------------------------------
# run_consolidation_job: pulos esperados nao interrompem o job
# ---------------------------------------------------------------------------

def _stub_consolidator(monkeypatch, error):
    attempted = []

    class StubConsolidator:
        def __init__(self, db):
            pass

        def consolidate_user_memories(self, user_id, lookback_days=90, relation_id=None):
            attempted.append(user_id)
            raise error

    monkeypatch.setattr(jmc, "MemoryConsolidator", StubConsolidator)
    return attempted


def _db_with_users(*user_ids):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE conversations (user_id TEXT)")
    for uid in user_ids:
        conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (uid,))
    conn.commit()

    class _DB:
        agent_instance = "jung_test"

        def resolve_relation_id(self, **kwargs):
            return "rel_1"

        def get_agent_relation(self, relation_id):
            return {"relation_id": "rel_1", "status": "active", "consent_status": "granted"}

    db = _DB()
    db.conn = conn
    return db


@pytest.mark.parametrize(
    "error",
    [
        ValueError("relation_scope_required_for_consolidation"),
        ValueError("relation_not_eligible_for_consolidation:status=revoked,consent=granted"),
        ValueError("consent_gate_unavailable_for_consolidation"),
    ],
)
def test_run_consolidation_job_skips_users_without_eligible_relation(monkeypatch, error):
    attempted = _stub_consolidator(monkeypatch, error)
    db = _db_with_users("user_a", "user_b")

    jmc.run_consolidation_job(db)  # nao deve levantar

    assert sorted(attempted) == ["user_a", "user_b"]


def test_run_consolidation_job_survives_other_errors(monkeypatch):
    attempted = _stub_consolidator(monkeypatch, RuntimeError("boom"))
    db = _db_with_users("user_a", "user_b")

    jmc.run_consolidation_job(db)

    assert sorted(attempted) == ["user_a", "user_b"]


# ---------------------------------------------------------------------------
# scheduler diario
# ---------------------------------------------------------------------------

def test_memory_consolidation_scheduler_runs_job_and_survives_errors(monkeypatch):
    """O scheduler agenda o job (promessa do /meu_perfil) e continua apos erros."""
    calls = []
    sleeps = []

    async def fake_job(db):
        calls.append(db)
        if len(calls) == 1:
            raise RuntimeError("boom")

    async def fake_sleep(*args, **kwargs):
        sleeps.append(args)
        if len(sleeps) >= 2:
            raise RuntimeError("stop-test")

    monkeypatch.setattr(jmc, "run_consolidation_job_async", fake_job)
    monkeypatch.setattr(jmc.asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop-test"):
        asyncio.run(jmc.memory_consolidation_scheduler(object(), initial_delay=0))

    # primeiro ciclo errou sem derrubar o loop; segundo ciclo rodou
    assert len(calls) == 2
