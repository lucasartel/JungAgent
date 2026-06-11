"""
test_diary_anchors.py - Testa ancoras de evidencia e utilitarios puros de agent_diary.py.

Circuitos cobertos:
  - PROFILE_SOURCE_RE: aceita e rejeita formatos corretos e invalidos
  - _as_text: truncamento com len(resultado) <= limit, normalizacao de whitespace, None
  - _json_loads: JSON valido, invalido, None, dict/list ja pronto
  - _safe_float: valores numericos, strings numericas, None, invalidos
"""
from __future__ import annotations

import pytest

from agent_diary import PROFILE_SOURCE_RE, _as_text, _json_loads, _safe_float


# ---------------------------------------------------------------------------
# 1. PROFILE_SOURCE_RE -- tipos validos
# ---------------------------------------------------------------------------

VALID_ANCHORS = [
    "conversation#1",
    "conversation#1423",
    "dream#87",
    "loop#205",
    "loop#1",
    "will#9",
    "meta#0",
    "rumination_insight#12",
    "work_run#3",
    "work_ticket#999",
    "work_delivery#1",
    "hobby_artifact#7",
    "agent_development#42",
]

INVALID_ANCHORS = [
    "conversation#",          # sem id numerico
    "dream-87",               # separador errado
    "loop#abc",               # id nao numerico
    "#123",                   # sem tipo
    "unknown_type#5",         # tipo nao reconhecido
    "CONVERSATION#1",         # maiusculo nao reconhecido
    "loop# 1",                # espaco antes do id
    "loop #1",                # espaco antes do #
    "conversation##1",        # duplo hash
    "dream#87x",              # sufixo nao numerico colado -- nao match por \b
]


class TestProfileSourceRe:
    @pytest.mark.parametrize("anchor", VALID_ANCHORS)
    def test_valid_anchor_matches(self, anchor):
        matches = PROFILE_SOURCE_RE.findall(anchor)
        assert anchor in matches, f"Esperava match para '{anchor}'"

    @pytest.mark.parametrize("anchor", INVALID_ANCHORS)
    def test_invalid_anchor_does_not_match(self, anchor):
        matches = PROFILE_SOURCE_RE.findall(anchor)
        assert matches == [], f"Nao esperava match para '{anchor}', obteve {matches}"

    def test_finds_multiple_anchors_in_text(self):
        text = "Baseado em conversation#1423 e dream#87 e loop#205."
        matches = PROFILE_SOURCE_RE.findall(text)
        assert set(matches) == {"conversation#1423", "dream#87", "loop#205"}

    def test_mixed_text_only_extracts_valid(self):
        text = "loop#1 e dream-87 e work_run#3 e #nope"
        matches = PROFILE_SOURCE_RE.findall(text)
        assert set(matches) == {"loop#1", "work_run#3"}

    def test_empty_string_produces_no_match(self):
        assert PROFILE_SOURCE_RE.findall("") == []

    def test_all_valid_types_matched(self):
        """Garante que todos os tipos documentados no CLAUDE.md sao reconhecidos."""
        types_to_check = [
            "loop", "conversation", "dream", "will", "meta",
            "rumination_insight", "work_run", "work_ticket", "work_delivery",
            "hobby_artifact", "agent_development",
        ]
        for t in types_to_check:
            anchor = f"{t}#1"
            matches = PROFILE_SOURCE_RE.findall(anchor)
            assert anchor in matches, f"Tipo '{t}' nao reconhecido pela regex"

    def test_word_boundary_prevents_partial_match(self):
        # 'dreamx#1' nao deve dar match porque 'dreamx' nao e um tipo valido
        assert PROFILE_SOURCE_RE.findall("dreamx#1") == []

    def test_anchor_in_sentence_extracted_correctly(self):
        text = "O insight de rumination_insight#99 foi integrado."
        matches = PROFILE_SOURCE_RE.findall(text)
        assert "rumination_insight#99" in matches

    def test_anchor_zero_id(self):
        # Id zero e tecnicamente valido pela regex (r'\d+')
        assert PROFILE_SOURCE_RE.findall("loop#0") == ["loop#0"]

    def test_large_id(self):
        assert PROFILE_SOURCE_RE.findall("conversation#999999") == ["conversation#999999"]


# ---------------------------------------------------------------------------
# 2. _as_text
#
# Contrato corrigido: len(resultado) <= limit sempre.
# Implementacao: texto curto retorna intacto; texto longo usa
#   text[:max(0, limit-3)].rstrip() + "..."
# Casos de borda: limit <= 3 retorna "..."[:max(0, limit)]
# ---------------------------------------------------------------------------

class TestAsText:
    def test_none_returns_empty_string(self):
        assert _as_text(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert _as_text("") == ""

    def test_whitespace_only_returns_empty_string(self):
        assert _as_text("   ") == ""

    def test_normal_string_unchanged(self):
        assert _as_text("hello world") == "hello world"

    def test_truncates_long_string(self):
        # Contrato: len(resultado) <= limit
        # text[:limit-3].rstrip() + "..." = "a"*317 + "..." = 320 chars
        s = "a" * 400
        result = _as_text(s, limit=320)
        assert result.endswith("...")
        assert len(result) <= 320
        assert result == "a" * 317 + "..."

    def test_no_truncation_at_exact_limit(self):
        s = "a" * 320
        result = _as_text(s, limit=320)
        assert result == s
        assert not result.endswith("...")

    def test_len_never_exceeds_limit(self):
        """Invariante principal: resultado nunca ultrapassa limit."""
        s = "x" * 500
        for lim in [1, 2, 3, 4, 10, 100, 320]:
            result = _as_text(s, limit=lim)
            assert len(result) <= lim, f"len={len(result)} excedeu limit={lim}"

    def test_normalizes_internal_whitespace(self):
        assert _as_text("hello   \n  world") == "hello world"

    def test_leading_trailing_whitespace_stripped(self):
        assert _as_text("  hello  ") == "hello"

    def test_integer_converted_to_string(self):
        result = _as_text(42)
        assert result == "42"

    def test_zero_limit_returns_empty(self):
        # limit=0 => "..."[:0] = ""
        result = _as_text("hello world", limit=0)
        assert result == ""
        assert len(result) <= 0

    def test_limit_1_returns_single_dot(self):
        # limit=1 => "..."[:1] = "."
        result = _as_text("hello world", limit=1)
        assert result == "."
        assert len(result) <= 1

    def test_limit_2_returns_two_dots(self):
        # limit=2 => "..."[:2] = ".."
        result = _as_text("hello world", limit=2)
        assert result == ".."
        assert len(result) <= 2

    def test_limit_3_returns_ellipsis(self):
        # limit=3 => "..."[:3] = "..."
        result = _as_text("hello world", limit=3)
        assert result == "..."
        assert len(result) <= 3

    def test_custom_limit_truncates(self):
        # limit=10 => text[:7].rstrip() + "..." = "aaaaaaa..."
        s = "a" * 50
        result = _as_text(s, limit=10)
        assert result == "a" * 7 + "..."
        assert len(result) <= 10

    def test_list_converted_to_string(self):
        result = _as_text([1, 2, 3])
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# 3. _json_loads
# ---------------------------------------------------------------------------

class TestJsonLoads:
    def test_valid_json_string(self):
        assert _json_loads('{"key": "value"}') == {"key": "value"}

    def test_valid_json_list(self):
        assert _json_loads('[1, 2, 3]') == [1, 2, 3]

    def test_none_returns_default(self):
        assert _json_loads(None) is None
        assert _json_loads(None, default={}) == {}

    def test_invalid_json_returns_default(self):
        assert _json_loads("not_json") is None
        assert _json_loads("not_json", default=[]) == []

    def test_dict_passthrough(self):
        d = {"a": 1}
        assert _json_loads(d) is d

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert _json_loads(lst) is lst

    def test_empty_string_returns_default(self):
        # json.loads("") levanta ValueError
        result = _json_loads("")
        assert result is None

    def test_nested_json(self):
        j = '{"outer": {"inner": [1, 2]}}'
        result = _json_loads(j)
        assert result["outer"]["inner"] == [1, 2]

    def test_custom_default_returned_on_error(self):
        sentinel = object()
        result = _json_loads("bad json", default=sentinel)
        assert result is sentinel


# ---------------------------------------------------------------------------
# 4. _safe_float
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_float_passthrough(self):
        assert _safe_float(3.14) == pytest.approx(3.14)

    def test_int_converted(self):
        assert _safe_float(5) == pytest.approx(5.0)

    def test_string_float(self):
        assert _safe_float("2.5") == pytest.approx(2.5)

    def test_string_int(self):
        assert _safe_float("10") == pytest.approx(10.0)

    def test_none_returns_default(self):
        assert _safe_float(None) == pytest.approx(0.0)
        assert _safe_float(None, default=99.0) == pytest.approx(99.0)

    def test_invalid_string_returns_default(self):
        assert _safe_float("abc") == pytest.approx(0.0)
        assert _safe_float("abc", default=-1.0) == pytest.approx(-1.0)

    def test_empty_string_returns_default(self):
        assert _safe_float("") == pytest.approx(0.0)

    def test_zero(self):
        assert _safe_float(0) == pytest.approx(0.0)

    def test_negative_float(self):
        assert _safe_float(-0.5) == pytest.approx(-0.5)

    def test_list_returns_default(self):
        assert _safe_float([1, 2]) == pytest.approx(0.0)
