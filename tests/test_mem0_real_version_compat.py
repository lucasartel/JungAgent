"""Compatibilidade com uma versao REAL instalada do mem0 (C12g).

Prova que a deteccao de capacidade de ``mem0_memory_adapter._namespace_query``
nunca produz kwargs que a API real rejeite — exatamente o ``TypeError`` que uma
assinatura intermediaria (``filters`` sem ``top_k``/``show_expired``) provocaria
— e que o parser de resposta aceita a chave documentada na versao instalada.

Pulado quando o mem0 nao esta instalado: ``requirements-test.txt`` nao inclui
``mem0ai`` (o ``requirements.txt`` de producao tem ``mem0ai>=0.1.0``), entao a
CI roda sem ele. O motivo do skip esta declarado para nao passar por verde
silencioso.

O conftest troca deps pesadas por modulos VAZIOS, que impedem importar o mem0
real. A fixture ``real_memory`` suspende so esses stubs, importa o mem0 e
RESTAURA o ``sys.modules`` ao final — o resto da sessao de testes continua
vendo os stubs como antes.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Stubs para dependencias opcionais de provider (padrao do repo).
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

# Pacotes que o mem0 real importa e que o conftest stuba como vazios.
_REAL_TOP_LEVEL = ("pydantic", "qdrant_client", "openai", "httpx", "anthropic")

# Motivos aceitos de capacidade nao comprovada (viram ``unknown``, nunca limpo).
LIMITED_REASONS = {
    "expired_unverifiable",
    "no_scope_parameter",
    "no_limit_parameter",
    "signature_unavailable",
}


def _really_installed(name: str) -> bool:
    try:
        return importlib.machinery.PathFinder.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _suspend_provider_stubs() -> None:
    """Remove os stubs vazios dos pacotes que o mem0 real precisa de verdade.

    So remove o que for stub (sem ``__file__``) E realmente instalado: num
    ambiente sem o pacote real o stub permanece e o import do mem0 e pulado.
    """
    restored_tops = []
    for name in _REAL_TOP_LEVEL:
        stub = sys.modules.get(name)
        if stub is None or getattr(stub, "__file__", None) is not None:
            continue  # nao carregado, ou ja e o pacote real
        if not _really_installed(name):
            continue  # pacote real indisponivel: mantem o stub
        del sys.modules[name]
        restored_tops.append(name)
    # Filhos stubados cujo pai acabou de virar o pacote real.
    for key in list(sys.modules):
        if "." not in key:
            continue
        module = sys.modules[key]
        if getattr(module, "__file__", None) is not None:
            continue
        if key.split(".", 1)[0] in restored_tops:
            del sys.modules[key]


if importlib.util.find_spec("mem0") is None:
    pytest.skip("mem0ai nao instalado neste ambiente", allow_module_level=True)

from mem0_memory_adapter import relation_namespace

NAMESPACE = relation_namespace("rel-1")


@pytest.fixture(scope="module")
def real_memory():
    """Importa ``Memory`` do mem0 real e restaura o ``sys.modules`` ao sair."""
    snapshot = dict(sys.modules)
    _suspend_provider_stubs()
    try:
        from mem0.memory.main import Memory
    except Exception as exc:
        pytest.skip(
            f"mem0 real nao importavel neste ambiente: {type(exc).__name__}: {exc}"
        )
    yield Memory
    for key in list(sys.modules):
        if key not in snapshot:
            del sys.modules[key]
    for key, module in snapshot.items():
        if module is not None:
            sys.modules[key] = module


def _bound(method):
    """Metodo nao vinculado -> vinculado a um objeto qualquer, so para assinatura."""
    return method.__get__(object())


def test_real_get_all_accepts_the_kwargs_we_would_send(real_memory):
    bound = _bound(real_memory.get_all)
    from mem0_memory_adapter import NamespaceReadUnsupported, _namespace_query

    try:
        kwargs = _namespace_query(NAMESPACE, bound)
    except NamespaceReadUnsupported as exc:
        # Versao sem capacidade comprovada: estado desconhecido, sem leitura.
        assert str(exc) in LIMITED_REASONS, str(exc)
        return

    # Se a capacidade foi comprovada, os kwargs DEVEM ser aceitos pela API real.
    inspect.signature(bound).bind(**kwargs)
    assert kwargs["show_expired"] is True
    limit = kwargs.get("top_k", kwargs.get("limit"))
    assert isinstance(limit, int) and limit > 0
    assert "user_id" not in kwargs  # user_id de topo so na forma legada


def test_real_get_all_never_gets_a_parameter_it_lacks(real_memory):
    """Nenhum kwarg enviado existe fora da assinatura real."""
    from mem0_memory_adapter import NamespaceReadUnsupported, _namespace_query

    bound = _bound(real_memory.get_all)
    parameters = inspect.signature(bound).parameters
    try:
        kwargs = _namespace_query(NAMESPACE, bound)
    except NamespaceReadUnsupported:
        return
    assert set(kwargs) <= set(parameters), set(kwargs) - set(parameters)


def test_real_delete_all_accepts_our_call_form(real_memory):
    """A chamada de apagagem usa a forma que a API real aceita."""
    bound = _bound(real_memory.delete_all)
    inspect.signature(bound).bind(user_id=NAMESPACE)


def test_real_documented_response_keys_are_parsed(real_memory):
    """A chave de resposta documentada na versao instalada passa pelo parser."""
    from mem0_memory_adapter import _namespace_rows

    doc = inspect.getdoc(real_memory.get_all) or ""
    assert doc, "get_all sem documentacao: nao ha forma de resposta a conferir"

    checked = 0
    for key in ("results", "memories"):
        if f'"{key}"' in doc:
            assert len(_namespace_rows({key: [{"memory": "x"}, {"id": "y"}]})) == 2
            checked += 1
    # Toda versao publicada devolve uma dessas duas formas.
    assert checked >= 1, f"chaves de resposta nao reconhecidas no doc: {doc[:200]}"
