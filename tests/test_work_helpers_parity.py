"""Corte 1: helpers re-importados de work.common; DEFAULT_PROVIDER_SPECS integro. Offline."""
from __future__ import annotations

import os
import pytest

from work.common import (
    _extract_package_text, _extract_theme_from_work_seed, _has_any_term,
    _has_non_numeric_terms, _json_loads_maybe, _keyword_overlap,
    _looks_like_objective_echo, _now_iso, _slugify, _truncate,
    _validate_destination_url,
)
from work.providers import DEFAULT_PROVIDER_SPECS as PROVIDER_DEFAULT_PROVIDER_SPECS
from work.engine import (
    DEFAULT_PROVIDER_SPECS,
    _extract_package_text as we_extract_package_text,
    _extract_theme_from_work_seed as we_extract_theme,
    _has_any_term as we_has_any_term,
    _has_non_numeric_terms as we_has_non_numeric_terms,
    _json_loads_maybe as we_json_loads_maybe,
    _looks_like_objective_echo as we_looks_like_objective_echo,
    _now_iso as we_now_iso,
    _slugify as we_slugify,
    _truncate as we_truncate,
)


class TestSymbolIdentity:
    """Helpers em work.engine sao os mesmos objetos de work.common (import, nao copia)."""

    def test_slugify_same(self):        assert we_slugify is _slugify
    def test_truncate_same(self):       assert we_truncate is _truncate
    def test_now_iso_same(self):        assert we_now_iso is _now_iso
    def test_json_loads_same(self):     assert we_json_loads_maybe is _json_loads_maybe
    def test_echo_same(self):           assert we_looks_like_objective_echo is _looks_like_objective_echo
    def test_pkg_text_same(self):       assert we_extract_package_text is _extract_package_text
    def test_has_any_term_same(self):   assert we_has_any_term is _has_any_term
    def test_theme_same(self):          assert we_extract_theme is _extract_theme_from_work_seed
    def test_non_numeric_same(self):    assert we_has_non_numeric_terms is _has_non_numeric_terms


class TestHelperBehavior:
    """Smoke tests de comportamento offline."""

    def test_slugify_ascii(self):
        assert _slugify("Ola Mundo!") == "ola-mundo"

    def test_slugify_accented(self):
        assert _slugify("Individuacao Junguiana") == "individuacao-junguiana"

    def test_slugify_empty_fallback(self):
        assert _slugify("") == "endojung-post"

    def test_validate_url_valid_https(self):
        os.environ["ALLOW_PRIVATE_WORK_DESTINATIONS"] = "1"
        try:
            _validate_destination_url("https://example.com/blog")
        finally:
            os.environ.pop("ALLOW_PRIVATE_WORK_DESTINATIONS", None)

    def test_validate_url_rejects_ftp(self):
        with pytest.raises(ValueError, match="http"):
            _validate_destination_url("ftp://example.com")

    def test_keyword_overlap_identical(self):
        assert _keyword_overlap("machine learning neural", "machine learning neural") == 1.0

    def test_keyword_overlap_disjoint(self):
        assert _keyword_overlap("apple orange banana", "car bicycle truck") == 0.0

    def test_keyword_overlap_partial(self):
        assert 0.0 < _keyword_overlap("deep learning model", "learning algorithm model") <= 1.0


class TestDefaultProviderSpecs:
    """DEFAULT_PROVIDER_SPECS em work.engine tem as 7 entradas esperadas."""

    def test_work_engine_reexports_provider_specs(self):
        assert DEFAULT_PROVIDER_SPECS is PROVIDER_DEFAULT_PROVIDER_SPECS

    def test_executable_providers(self):
        assert DEFAULT_PROVIDER_SPECS["wordpress"]["status"] == "executable"
        assert DEFAULT_PROVIDER_SPECS["github"]["status"] == "executable"

    def test_planned_providers_present(self):
        for key in ("google_drive", "google_calendar", "railway", "google", "asana"):
            assert key in DEFAULT_PROVIDER_SPECS, f"Missing: {key}"

    def test_wordpress_credential_fields(self):
        fields = [f["name"] for f in DEFAULT_PROVIDER_SPECS["wordpress"]["credential_schema"]["fields"]]
        assert "base_url" in fields and "application_password" in fields

    def test_github_guardrails(self):
        gr = DEFAULT_PROVIDER_SPECS["github"]["guardrails"]
        assert "pull_request_required" in gr and "no_direct_push_to_main" in gr
