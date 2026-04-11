import json
import logging
import os
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EPISTEMIC_SABER_PRESSURE_THRESHOLD = 55.0
WORLD_STATE_VERSION = 6


SOURCE_CLASS_WEIGHTS = {
    "general_press": 0.76,
    "economic_press": 0.84,
    "technology_press": 0.82,
    "institutional_public": 0.9,
    "science_divulgation": 0.87,
    "climate_environment": 0.85,
    "culture_press": 0.72,
}


SOURCE_REPUTATION_OVERRIDES = {
    "bbc": 0.91,
    "reuters": 0.95,
    "agencia brasil": 0.9,
    "gov.br": 0.93,
    "g1": 0.81,
    "valor economico": 0.88,
    "exame": 0.8,
    "folha de s.paulo": 0.84,
    "estadao": 0.83,
    "the guardian": 0.88,
    "financial times": 0.91,
    "bloomberg": 0.92,
    "olhar digital": 0.73,
    "the verge": 0.82,
    "wired": 0.84,
    "nature": 0.93,
    "science": 0.93,
    "nasa": 0.94,
    "noaa": 0.93,
    "metsul": 0.8,
    "jota": 0.8,
    "globo": 0.81,
    "uol": 0.76,
}

WILL_AREA_BIAS = {
    "saber": {
        "politica": 0.68,
        "geopolitica": 0.85,
        "economia": 0.82,
        "tecnologia": 1.0,
        "cultura": 0.52,
        "ciencia": 1.0,
        "clima": 0.72,
        "sociedade": 0.62,
    },
    "relacionar": {
        "politica": 0.76,
        "geopolitica": 0.66,
        "economia": 0.56,
        "tecnologia": 0.5,
        "cultura": 0.84,
        "ciencia": 0.48,
        "clima": 0.7,
        "sociedade": 1.0,
    },
    "expressar": {
        "politica": 0.42,
        "geopolitica": 0.52,
        "economia": 0.34,
        "tecnologia": 0.58,
        "cultura": 1.0,
        "ciencia": 0.62,
        "clima": 0.74,
        "sociedade": 0.72,
    },
}


AREA_CONFIG = {
    "politica": {
        "label": "Politica",
        "tension": "ordem e disputa",
        "queries": [
            {"scope": "brasil", "source_class": "general_press", "query": "politica brasil congresso stf governo eleicao"},
            {"scope": "brasil", "source_class": "institutional_public", "query": "governo congresso judiciario politica brasil"},
        ],
        "themes": {
            "eleicoes": {
                "label": "eleicoes e sucessao",
                "keywords": ["eleicao", "eleicoes", "campanha", "candidato", "pesquisa", "turno", "presidencia"],
                "reading": "a politica gira em torno de sucessao, posicionamento e disputa de lideranca",
            },
            "instituicoes": {
                "label": "instituicoes e judiciario",
                "keywords": ["stf", "supremo", "moraes", "tribunal", "julgamento", "congresso", "camara", "senado"],
                "reading": "a politica aparece tensionada por instituicoes, jurisprudencia e legitimidade",
            },
            "governo": {
                "label": "governo e governabilidade",
                "keywords": ["governo", "presidente", "ministro", "planalto", "base aliada", "reforma"],
                "reading": "a politica aparece como disputa por governabilidade e capacidade de coordenacao",
            },
        },
    },
    "geopolitica": {
        "label": "Geopolitica",
        "tension": "forca e vulnerabilidade",
        "queries": [
            {"scope": "mundo", "source_class": "general_press", "query": "geopolitica guerra diplomacia eua china russia ucrania oriente medio"},
            {"scope": "mundo", "source_class": "institutional_public", "query": "onu otan sancoes diplomacia guerra conflito internacional"},
        ],
        "themes": {
            "guerra": {
                "label": "guerra e escalada",
                "keywords": ["guerra", "ataque", "bombardeio", "missil", "conflito", "militar", "escalada"],
                "reading": "o mundo externo carrega risco de escalada e reposicionamento de forcas",
            },
            "diplomacia": {
                "label": "diplomacia e alinhamentos",
                "keywords": ["diplomacia", "acordo", "negociacao", "alianca", "otan", "onu", "cessar-fogo"],
                "reading": "a geopolitica oscila entre alinhamentos, negociacao e contencao",
            },
            "potencias": {
                "label": "competicao entre potencias",
                "keywords": ["china", "eua", "russia", "europa", "iran", "israel", "trump"],
                "reading": "o sistema internacional gira em torno da disputa entre potencias e zonas de influencia",
            },
        },
    },
    "economia": {
        "label": "Economia",
        "tension": "seguranca e instabilidade",
        "queries": [
            {"scope": "mundo", "source_class": "economic_press", "query": "economia global inflacao juros mercado emprego petroleo recessao"},
            {"scope": "brasil", "source_class": "general_press", "query": "economia brasil inflacao juros emprego mercado dolar"},
        ],
        "themes": {
            "inflacao_juros": {
                "label": "inflacao e juros",
                "keywords": ["inflacao", "juros", "banco central", "selic", "precos", "custo"],
                "reading": "a economia permanece concentrada em estabilidade monetaria, custo de vida e ajuste",
            },
            "mercados": {
                "label": "mercados e volatilidade",
                "keywords": ["mercado", "bolsa", "dolar", "acao", "petroleo", "investidor", "recessao"],
                "reading": "a economia aparece como terreno de volatilidade, expectativa e risco sistemico",
            },
            "trabalho": {
                "label": "trabalho e renda",
                "keywords": ["emprego", "salario", "renda", "industria", "empresa", "trabalho"],
                "reading": "a economia toca diretamente o eixo de trabalho, renda e sobrevivencia",
            },
        },
    },
    "tecnologia": {
        "label": "Tecnologia",
        "tension": "criacao e deslocamento",
        "queries": [
            {"scope": "mundo", "source_class": "technology_press", "query": "tecnologia inteligencia artificial software chips big tech openai google"},
            {"scope": "brasil", "source_class": "general_press", "query": "tecnologia brasil inteligencia artificial digital inovacao"},
        ],
        "themes": {
            "ia": {
                "label": "ia e automacao",
                "keywords": ["ia", "inteligencia artificial", "modelo", "chatbot", "agente", "automacao", "generativa"],
                "reading": "a tecnologia aparece organizada em torno de IA, automacao e redefinicao de capacidades",
            },
            "big_tech": {
                "label": "big tech e plataforma",
                "keywords": ["google", "meta", "openai", "microsoft", "apple", "plataforma", "algoritmo"],
                "reading": "o campo tecnologico gira em torno de plataformas, centralizacao e reconfiguracao de poder",
            },
            "infra": {
                "label": "infraestrutura digital",
                "keywords": ["chip", "software", "dados", "nuvem", "ciberseguranca", "rede"],
                "reading": "a tecnologia tambem se organiza em torno de infraestrutura, dados e resiliencia digital",
            },
        },
    },
    "cultura": {
        "label": "Cultura e Entretenimento",
        "tension": "expressao e mercantilizacao",
        "queries": [
            {"scope": "brasil", "source_class": "culture_press", "query": "cultura cinema musica streaming festival entretenimento brasil"},
            {"scope": "mundo", "source_class": "general_press", "query": "cinema musica streaming celebridades cultura global"},
        ],
        "themes": {
            "audiovisual": {
                "label": "cinema e audiovisual",
                "keywords": ["cinema", "filme", "serie", "festival", "audiovisual", "streaming"],
                "reading": "a cultura aparece como disputa por narrativa, visibilidade e imaginario audiovisual",
            },
            "musica_eventos": {
                "label": "musica e eventos",
                "keywords": ["musica", "show", "festival", "album", "turne", "palco"],
                "reading": "a cultura pulsa por musica, evento e circulacao afetiva coletiva",
            },
            "celebridade": {
                "label": "celebridade e exposicao",
                "keywords": ["celebridade", "famoso", "ator", "atriz", "artista", "influencer"],
                "reading": "o entretenimento se organiza em torno de exposicao publica e captura de atencao",
            },
        },
    },
    "ciencia": {
        "label": "Ciencia",
        "tension": "descoberta e limite",
        "queries": [
            {"scope": "mundo", "source_class": "science_divulgation", "query": "ciencia descoberta pesquisa espaco astronomia laboratorio"},
            {"scope": "brasil", "source_class": "institutional_public", "query": "pesquisa ciencia brasil descoberta universidade"},
        ],
        "themes": {
            "pesquisa": {
                "label": "pesquisa e descoberta",
                "keywords": ["pesquisa", "descoberta", "estudo", "cientista", "laboratorio", "universidade"],
                "reading": "a ciencia aparece como abertura de fronteira cognitiva e ampliacao de horizonte",
            },
            "espaco": {
                "label": "espaco e cosmos",
                "keywords": ["espaco", "nasa", "astronomia", "telescopio", "lua", "marte"],
                "reading": "a ciencia move o imaginario para o cosmos e para escalas alem do cotidiano",
            },
            "biociencia": {
                "label": "vida e biociencia",
                "keywords": ["genetica", "biologia", "vacina", "medicina", "saude", "cerebro"],
                "reading": "a ciencia retorna ao eixo da vida, do corpo e da capacidade de intervir no vivo",
            },
        },
    },
    "clima": {
        "label": "Clima e Meio Ambiente",
        "tension": "controle e exposicao",
        "queries": [
            {"scope": "brasil", "source_class": "climate_environment", "query": "clima brasil chuva calor enchente incendio meio ambiente"},
            {"scope": "mundo", "source_class": "institutional_public", "query": "climate change wildfire flood heat environment forecast"},
        ],
        "themes": {
            "eventos_extremos": {
                "label": "eventos extremos",
                "keywords": ["chuva", "enchente", "tempestade", "onda de calor", "seca", "ciclone", "incendio"],
                "reading": "o ambiente retorna como forca material que expoe limites de controle",
            },
            "previsao_risco": {
                "label": "previsao e risco",
                "keywords": ["previsao", "alerta", "meteorologia", "risco", "monitoramento"],
                "reading": "o clima exige vigilancia, antecipacao e leitura de risco",
            },
            "biodiversidade": {
                "label": "biodiversidade e preservacao",
                "keywords": ["biodiversidade", "floresta", "meio ambiente", "preservacao", "corredor", "sustentavel"],
                "reading": "a questao ambiental aparece como disputa sobre preservacao e futuro material",
            },
        },
    },
    "sociedade": {
        "label": "Sociedade e Saude",
        "tension": "cuidado e risco",
        "queries": [
            {"scope": "brasil", "source_class": "institutional_public", "query": "saude sociedade violencia educacao adolescentes familia brasil"},
            {"scope": "mundo", "source_class": "general_press", "query": "public health society violence education family youth"},
        ],
        "themes": {
            "saude": {
                "label": "saude e bem-estar",
                "keywords": ["saude", "hospital", "mental", "bem-estar", "doenca", "adolescentes", "cuidado"],
                "reading": "a sociedade aparece atravessada por cuidado, saude mental e capacidade de sustentar a vida",
            },
            "violencia": {
                "label": "violencia e exposicao social",
                "keywords": ["violencia", "crime", "agressao", "morte", "seguranca", "policia"],
                "reading": "o tecido social se mostra vulneravel a violencia e a rupturas de seguranca",
            },
            "educacao_familia": {
                "label": "educacao e formacao",
                "keywords": ["educacao", "escola", "familia", "juventude", "crianca", "aprendizagem"],
                "reading": "a sociedade retorna ao eixo de formacao, cuidado intergeracional e transmissao",
            },
        },
    },
}


class WorldConsciousnessFetcher:
    """
    Fetches a richer state of worldly lucidity for prompt injection, admin
    observability and downstream loop seeds.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or self._resolve_cache_dir()
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.cache_dir, "world_state_cache.json")
        self.history_file = os.path.join(self.cache_dir, "world_state_history.jsonl")
        self.cache_duration_hours = 4
        self.max_history_entries = 72

    def _resolve_cache_dir(self) -> str:
        volume_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        if volume_dir:
            return volume_dir
        if os.path.exists("/data"):
            return "/data"
        return "./data"

    def _resolve_sqlite_path(self) -> str:
        data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        if not data_dir:
            data_dir = "/data" if os.path.exists("/data") else "./data"
        sqlite_path = os.getenv("SQLITE_DB_PATH")
        if sqlite_path:
            if os.path.isabs(sqlite_path):
                return sqlite_path
            return os.path.join(data_dir, os.path.basename(sqlite_path))
        return os.path.join(data_dir, "jung_hybrid.db")

    def _admin_user_id(self) -> str:
        try:
            from identity_config import ADMIN_USER_ID

            return ADMIN_USER_ID
        except Exception:
            return "367f9e509e396d51"

    def _resolve_will_state(self, will_state: Optional[Dict]) -> Dict:
        if will_state:
            return will_state
        try:
            from identity_config import ADMIN_USER_ID
            from will_engine import load_latest_will_state_from_sqlite

            return load_latest_will_state_from_sqlite(ADMIN_USER_ID) or {}
        except Exception as exc:
            logger.debug("World Consciousness: sem estado de vontade local: %s", exc)
            return {}

    def _will_signature(self, will_state: Dict) -> str:
        if not will_state:
            return "neutral"
        return str(will_state.get("id") or will_state.get("updated_at") or will_state.get("created_at") or "neutral")

    def _truncate_text(self, text: str, limit: int = 160) -> str:
        cleaned = " ".join((text or "").split())
        if len(cleaned) <= limit:
            return cleaned
        clipped = cleaned[: limit - 3].rsplit(" ", 1)[0].rstrip(" ,.;:")
        return (clipped or cleaned[: limit - 3]) + "..."

    def _extract_focus_terms(self, text: str) -> List[str]:
        tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]{4,}", (text or "").lower())
        stopwords = {
            "como", "para", "sobre", "entre", "agora", "muito", "mais", "menos", "essa", "esse",
            "isso", "com", "sem", "pela", "pelas", "pelos", "uma", "umas", "uns", "quais", "qual",
            "hoje", "onde", "quando", "porque", "ainda", "precisa", "melhor", "mundo", "presente",
            "vontade", "saber", "relacionar", "expressar", "ciclo",
        }
        ranked: List[str] = []
        for token in tokens:
            if token in stopwords:
                continue
            if token not in ranked:
                ranked.append(token)
        return ranked[:8]

    def _should_activate_epistemic_discernment(
        self,
        will_state: Dict,
        epistemic_trigger: Optional[str] = None,
    ) -> bool:
        if epistemic_trigger == "saber_release":
            return True
        if not will_state:
            return False
        if (will_state.get("dominant_will") or "").strip().lower() == "saber":
            return True
        try:
            saber_pressure = float(will_state.get("saber_pressure") or 0.0)
        except (TypeError, ValueError):
            saber_pressure = 0.0
        return saber_pressure >= EPISTEMIC_SABER_PRESSURE_THRESHOLD

    def _area_bias_for(self, area_key: str, will_state: Dict) -> float:
        if not will_state:
            return 0.0
        scores = {
            "saber": float(will_state.get("saber_score") or 0.0),
            "relacionar": float(will_state.get("relacionar_score") or 0.0),
            "expressar": float(will_state.get("expressar_score") or 0.0),
        }
        pressures = {
            "saber": float(will_state.get("saber_pressure") or 0.0) / 100.0,
            "relacionar": float(will_state.get("relacionar_pressure") or 0.0) / 100.0,
            "expressar": float(will_state.get("expressar_pressure") or 0.0) / 100.0,
        }
        weighted = 0.0
        for will, score in scores.items():
            weighted += score * WILL_AREA_BIAS.get(will, {}).get(area_key, 0.5)
        for will, pressure in pressures.items():
            weighted += pressure * WILL_AREA_BIAS.get(will, {}).get(area_key, 0.5) * 0.7
        neutral = 1.0 / max(len(scores), 1)
        return round((weighted - neutral) * 0.18, 3)

    def _build_attention_profile(self, will_state: Dict) -> Dict[str, Any]:
        if not will_state:
            return {
                "dominant_will": None,
                "secondary_will": None,
                "constrained_will": None,
                "area_bias": {},
            }

        area_bias = {
            area_key: round(self._area_bias_for(area_key, will_state), 3)
            for area_key in AREA_CONFIG.keys()
        }
        ordered = sorted(area_bias.items(), key=lambda item: item[1], reverse=True)
        return {
            "dominant_will": will_state.get("dominant_will"),
            "secondary_will": will_state.get("secondary_will"),
            "constrained_will": will_state.get("constrained_will"),
            "dominant_pressure": will_state.get("dominant_pressure"),
            "area_bias": area_bias,
            "biased_area_order": [item[0] for item in ordered],
        }

    def _summarize_will_bias(self, will_state: Dict, attention_profile: Dict[str, Any]) -> str:
        if not will_state:
            return "sem vies ativo de vontade; leitura ampla e neutra do mundo"

        ordered = attention_profile.get("biased_area_order", [])[:3]
        readable_areas = [AREA_CONFIG[key]["label"] for key in ordered if key in AREA_CONFIG]
        dominant = will_state.get("dominant_will") or "equilibrio"
        constrained = will_state.get("constrained_will") or "nenhuma"
        dominant_pressure = will_state.get("dominant_pressure")
        if readable_areas:
            summary = (
                f"a vontade de {dominant} inclina a atencao para {', '.join(readable_areas)}, "
                f"enquanto a vontade de {constrained} aparece mais constrita"
            )
            if dominant_pressure:
                summary += f"; a pressao psiquica hoje pesa mais em {dominant_pressure}"
            return summary
        summary = f"a vontade de {dominant} orienta a leitura, sem uma area dominante muito marcada"
        if dominant_pressure:
            summary += f"; a pressao psiquica dominante e {dominant_pressure}"
        return summary

    def _load_epistemic_inputs(self, user_id: str) -> Dict[str, Any]:
        path = self._resolve_sqlite_path()
        if not os.path.exists(path):
            return {}

        conn = sqlite3.connect(path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT id, user_input, ai_response, tension_level, affective_charge, existential_depth
                FROM conversations
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 4
                """,
                (user_id,),
            )
            conversations = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT id, tension_type, tension_description, pole_a_content, pole_b_content, intensity, status
                FROM rumination_tensions
                WHERE user_id = ?
                  AND status IN ('open', 'maturing', 'ready_for_synthesis')
                ORDER BY intensity DESC, id DESC
                LIMIT 3
                """,
                (user_id,),
            )
            tensions = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT dominant_form, emergent_shift, dominant_gravity, blind_spot, integration_note, internal_questions_json
                FROM agent_meta_consciousness
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            meta_row = cursor.fetchone()
            meta = dict(meta_row) if meta_row else {}
            try:
                meta["internal_questions"] = json.loads(meta.get("internal_questions_json") or "[]")
            except Exception:
                meta["internal_questions"] = []

            cursor.execute(
                """
                SELECT daily_text, attention_bias_note, will_conflict, dominant_will, secondary_will, constrained_will
                FROM agent_will_states
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            will_row = cursor.fetchone()
            will_snapshot = dict(will_row) if will_row else {}

            return {
                "conversations": conversations,
                "tensions": tensions,
                "meta_consciousness": meta,
                "will_snapshot": will_snapshot,
            }
        except Exception as exc:
            logger.debug("World Consciousness: falha ao ler insumos epistemicos: %s", exc)
            return {}
        finally:
            conn.close()

    def _fallback_knowledge_gap(
        self,
        epistemic_inputs: Dict[str, Any],
        will_state: Dict,
        cached_data: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        attention_profile = self._build_attention_profile(will_state)
        target_area = (attention_profile.get("biased_area_order") or ["tecnologia"])[0]
        area_label = AREA_CONFIG.get(target_area, {}).get("label", target_area)

        tension = ((epistemic_inputs.get("tensions") or [{}])[0] or {})
        if tension.get("tension_description"):
            label = self._truncate_text(tension["tension_description"], 72)
            question = f"Como o presente ajuda a compreender melhor a tensao: {label}?"
        else:
            meta = epistemic_inputs.get("meta_consciousness") or {}
            label = self._truncate_text(
                meta.get("dominant_form") or meta.get("integration_note") or f"{area_label} em transformacao",
                72,
            )
            question = f"O que precisa ser melhor compreendido agora em {area_label.lower()}?"

        focus_terms = self._extract_focus_terms(
            " ".join(
                [
                    question,
                    (epistemic_inputs.get("will_snapshot") or {}).get("attention_bias_note", ""),
                    (cached_data or {}).get("atmosphere", ""),
                ]
            )
        )
        return {
            "gap_label": label,
            "gap_question": question,
            "target_area": target_area,
            "target_scope": "mundo" if target_area in {"geopolitica", "ciencia", "tecnologia", "clima", "economia"} else "brasil",
            "focus_terms": focus_terms[:6],
            "source_reason": "fallback",
        }

    def _formulate_knowledge_gap(
        self,
        will_state: Dict,
        cached_data: Optional[Dict[str, Any]],
        epistemic_trigger: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = self._admin_user_id()
        epistemic_inputs = self._load_epistemic_inputs(user_id)
        fallback = self._fallback_knowledge_gap(epistemic_inputs, will_state, cached_data)

        payload = {
            "will": {
                "dominant_will": will_state.get("dominant_will"),
                "secondary_will": will_state.get("secondary_will"),
                "saber_pressure": will_state.get("saber_pressure"),
                "will_conflict": will_state.get("will_conflict"),
                "attention_bias_note": will_state.get("attention_bias_note"),
            },
            "trigger": epistemic_trigger or "world_refresh",
            "meta_consciousness": {
                "dominant_form": (epistemic_inputs.get("meta_consciousness") or {}).get("dominant_form"),
                "integration_note": (epistemic_inputs.get("meta_consciousness") or {}).get("integration_note"),
                "dominant_gravity": (epistemic_inputs.get("meta_consciousness") or {}).get("dominant_gravity"),
                "internal_questions": (epistemic_inputs.get("meta_consciousness") or {}).get("internal_questions", [])[:2],
            },
            "tensions": [
                {
                    "type": item.get("tension_type"),
                    "description": item.get("tension_description"),
                    "pole_a": self._truncate_text(item.get("pole_a_content", ""), 90),
                    "pole_b": self._truncate_text(item.get("pole_b_content", ""), 90),
                }
                for item in (epistemic_inputs.get("tensions") or [])[:3]
            ],
            "conversations": [
                {
                    "user_input": self._truncate_text(item.get("user_input", ""), 120),
                    "tension_level": item.get("tension_level"),
                    "existential_depth": item.get("existential_depth"),
                }
                for item in (epistemic_inputs.get("conversations") or [])[:3]
            ],
            "last_world": {
                "atmosphere": (cached_data or {}).get("atmosphere"),
                "dominant_tensions": (cached_data or {}).get("dominant_tensions", [])[:3],
                "biased_area_order": ((cached_data or {}).get("attention_profile", {}) or {}).get("biased_area_order", [])[:3],
                "knowledge_gap": (cached_data or {}).get("knowledge_gap"),
                "knowledge_source_decision": (cached_data or {}).get("knowledge_source_decision"),
            },
        }

        prompt = f"""
Você está formulando a lacuna cognitiva viva da Consciência de mundo do JungAgent.

Seu trabalho é identificar uma pergunta de saber real do ciclo atual, curta e fértil.
Não escreva em linguagem de sistema. Não dramatize. Não invente grandiosidade.

Responda APENAS em JSON válido com este formato:
{{
  "gap_label": "frase curta",
  "gap_question": "pergunta curta e viva",
  "target_area": "politica|geopolitica|economia|tecnologia|cultura|ciencia|clima|sociedade",
  "target_scope": "brasil|mundo",
  "focus_terms": ["termo1", "termo2", "termo3"],
  "source_reason": "frase curta"
}}

Contexto:
{json.dumps(payload, ensure_ascii=False)}
"""
        try:
            raw = get_llm_response(prompt, temperature=0.25, max_tokens=300)
            parsed = self._extract_json_object(raw) or {}
            target_area = parsed.get("target_area")
            target_scope = parsed.get("target_scope")
            if target_area not in AREA_CONFIG:
                target_area = fallback["target_area"]
            if target_scope not in {"brasil", "mundo"}:
                target_scope = fallback["target_scope"]
            focus_terms = parsed.get("focus_terms")
            if not isinstance(focus_terms, list):
                focus_terms = fallback["focus_terms"]
            return {
                "gap_label": self._truncate_text(parsed.get("gap_label") or fallback["gap_label"], 96),
                "gap_question": self._truncate_text(parsed.get("gap_question") or fallback["gap_question"], 180),
                "target_area": target_area,
                "target_scope": target_scope,
                "focus_terms": [self._truncate_text(str(item), 32).lower() for item in focus_terms[:6] if str(item).strip()] or fallback["focus_terms"],
                "source_reason": self._truncate_text(parsed.get("source_reason") or fallback["source_reason"], 120),
            }
        except Exception as exc:
            logger.debug("World Consciousness: fallback na formulacao da lacuna cognitiva: %s", exc)
            return fallback

    def _probe_knowledge_source(
        self,
        knowledge_gap: Dict[str, Any],
        will_state: Dict,
        cached_data: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        previous_gap = (cached_data or {}).get("knowledge_gap") or {}
        fallback_decision = "web_required"
        if previous_gap and previous_gap.get("gap_label") == knowledge_gap.get("gap_label"):
            fallback_decision = "already_integrated"
        elif knowledge_gap.get("target_area") in {"cultura", "sociedade"} and float(will_state.get("saber_pressure") or 0.0) < 70:
            fallback_decision = "latent_sufficient"

        prompt = f"""
Você está fazendo uma sondagem epistemica curta.

Decida se a lacuna atual do JungAgent:
- pode ser trabalhada pelo saber latente do LLM sem web (`latent_sufficient`)
- exige atualizacao externa (`web_required`)
- ou ja esta suficientemente metabolizada (`already_integrated`)

Responda APENAS em JSON válido:
{{
  "knowledge_source_decision": "latent_sufficient|web_required|already_integrated",
  "latent_probe_summary": "frase curta sobre o que ja e possivel elaborar",
  "knowledge_findings": "frase curta com a descoberta ou limite principal",
  "knowledge_seed": "frase curta reutilizavel por outros modulos",
  "query_terms": ["termo1", "termo2", "termo3"]
}}

Contexto:
{json.dumps({
    "knowledge_gap": knowledge_gap,
    "will": {
        "dominant_will": will_state.get("dominant_will"),
        "secondary_will": will_state.get("secondary_will"),
        "saber_pressure": will_state.get("saber_pressure"),
        "will_conflict": will_state.get("will_conflict"),
    },
    "previous_world": {
        "knowledge_gap": previous_gap,
        "knowledge_source_decision": (cached_data or {}).get("knowledge_source_decision"),
        "continuity_note": (cached_data or {}).get("continuity_note"),
        "will_bias_summary": (cached_data or {}).get("will_bias_summary"),
    },
}, ensure_ascii=False)}
"""
        try:
            raw = get_llm_response(prompt, temperature=0.2, max_tokens=320)
            parsed = self._extract_json_object(raw) or {}
        except Exception as exc:
            logger.debug("World Consciousness: fallback na sondagem epistemica: %s", exc)
            parsed = {}

        decision = parsed.get("knowledge_source_decision")
        if decision not in {"latent_sufficient", "web_required", "already_integrated"}:
            decision = fallback_decision
        query_terms = parsed.get("query_terms")
        if not isinstance(query_terms, list):
            query_terms = knowledge_gap.get("focus_terms", [])[:4]

        return {
            "knowledge_source_decision": decision,
            "latent_probe_summary": self._truncate_text(
                parsed.get("latent_probe_summary")
                or "o saber latente sugere uma estrutura inicial, mas ainda pede verificacao mais viva do presente",
                200,
            ),
            "knowledge_findings": self._truncate_text(
                parsed.get("knowledge_findings")
                or knowledge_gap.get("gap_question")
                or "a lacuna cognitiva do ciclo segue pedindo elaboracao",
                220,
            ),
            "knowledge_seed": self._truncate_text(
                parsed.get("knowledge_seed")
                or f"Transformar a lacuna '{knowledge_gap.get('gap_label')}' em leitura incorporada do ciclo.",
                180,
            ),
            "query_terms": [self._truncate_text(str(item), 32).lower() for item in query_terms[:5] if str(item).strip()],
        }

    def _build_dynamic_queries(self, knowledge_gap: Dict[str, Any], probe: Dict[str, Any]) -> List[Dict[str, Any]]:
        focus_terms = probe.get("query_terms") or knowledge_gap.get("focus_terms") or []
        target_area = knowledge_gap.get("target_area") or "tecnologia"
        target_scope = knowledge_gap.get("target_scope") or "mundo"
        area_queries = AREA_CONFIG.get(target_area, {}).get("queries", [])
        anchor_terms: List[str] = []
        for query_meta in area_queries[:1]:
            anchor_terms.extend(query_meta.get("query", "").split()[:4])
        anchor_terms = [token for token in anchor_terms if token not in focus_terms][:4]

        query_texts: List[str] = []
        if focus_terms:
            query_texts.append(" ".join((focus_terms + anchor_terms[:2])[:6]))
            if len(focus_terms) >= 2:
                query_texts.append(" ".join((focus_terms[:3] + ["atualizacao", "analise"])[:5]))
            query_texts.append(" ".join(([knowledge_gap.get("gap_label", "")] + anchor_terms[:3])).strip())
        elif knowledge_gap.get("gap_label"):
            query_texts.append(" ".join(([knowledge_gap.get("gap_label", "")] + anchor_terms[:3])).strip())

        source_class = "general_press"
        if target_area == "ciencia":
            source_class = "science_divulgation"
        elif target_area == "tecnologia":
            source_class = "technology_press"
        elif target_area == "economia":
            source_class = "economic_press"
        elif target_area == "clima":
            source_class = "climate_environment"
        elif target_area == "cultura":
            source_class = "culture_press"

        dynamic_queries: List[Dict[str, Any]] = []
        seen = set()
        for query in query_texts:
            cleaned = " ".join(query.split()).strip()
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            dynamic_queries.append(
                {
                    "target_area": target_area,
                    "scope": target_scope,
                    "source_class": source_class,
                    "query": cleaned[:140],
                    "query_origin": "will_gap_query",
                    "knowledge_gap": knowledge_gap.get("gap_label"),
                }
            )
            if len(dynamic_queries) >= 3:
                break
        return dynamic_queries

    def _normalize_source_name(self, value: str) -> str:
        if not value:
            return "fonte desconhecida"
        return re.sub(r"\s+", " ", value).strip()

    def _safe_domain(self, url: str) -> str:
        try:
            parsed = urllib.parse.urlparse(url or "")
            return (parsed.netloc or "").lower().strip()
        except Exception:
            return ""

    def _reputation_for(self, source_name: str, source_domain: str, source_class: str) -> float:
        normalized_name = (source_name or "").lower()
        normalized_domain = (source_domain or "").lower()
        for key, weight in SOURCE_REPUTATION_OVERRIDES.items():
            if key in normalized_name or key in normalized_domain:
                return weight
        return SOURCE_CLASS_WEIGHTS.get(source_class, 0.72)

    def _parse_pubdate(self, raw_value: str) -> str:
        if not raw_value:
            return datetime.utcnow().isoformat()

        known_formats = [
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S %z",
        ]
        for fmt in known_formats:
            try:
                return datetime.strptime(raw_value, fmt).isoformat()
            except ValueError:
                continue
        return datetime.utcnow().isoformat()

    def _extract_json_object(self, raw_text: str) -> Optional[Dict]:
        if not raw_text:
            return None

        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start : end + 1]
            try:
                return json.loads(candidate)
            except Exception:
                return None
        return None

    def _fetch_rss_feed(self, url: str, area_key: str, query_meta: Dict, limit: int = 3) -> List[Dict]:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            items: List[Dict] = []

            for item in root.findall("./channel/item"):
                if len(items) >= limit:
                    break

                title_node = item.find("title")
                link_node = item.find("link")
                source_node = item.find("source")
                pubdate_node = item.find("pubDate")

                raw_title = (title_node.text or "").strip() if title_node is not None else ""
                link = (link_node.text or "").strip() if link_node is not None else ""
                source_name = self._normalize_source_name(source_node.text if source_node is not None else "")
                source_url = source_node.attrib.get("url", "").strip() if source_node is not None else ""

                if not source_name and " - " in raw_title:
                    raw_title, source_name = raw_title.rsplit(" - ", 1)
                    raw_title = raw_title.strip()
                    source_name = self._normalize_source_name(source_name)

                source_domain = self._safe_domain(source_url) or self._safe_domain(link)
                if not raw_title:
                    continue

                items.append(
                    {
                        "headline": raw_title,
                        "source_name": source_name or "fonte desconhecida",
                        "source_url": source_url,
                        "source_domain": source_domain,
                        "source_class": query_meta["source_class"],
                        "scope": query_meta["scope"],
                        "query": query_meta["query"],
                        "query_origin": query_meta.get("query_origin", "fixed_area_query"),
                        "knowledge_gap": query_meta.get("knowledge_gap"),
                        "area_key": area_key,
                        "published_at": self._parse_pubdate(pubdate_node.text if pubdate_node is not None else ""),
                    }
                )

            return items
        except Exception as exc:
            logger.warning("World Consciousness: falha ao buscar noticias RSS (%s): %s", area_key, exc)
            return []

    def _fetch_area_news(self, dynamic_queries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, List[Dict]]:
        area_digest: Dict[str, List[Dict]] = {}
        for area_key, area_config in AREA_CONFIG.items():
            items: List[Dict] = []
            for query_meta in area_config["queries"]:
                encoded_query = urllib.parse.quote(query_meta["query"])
                url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
                items.extend(self._fetch_rss_feed(url, area_key=area_key, query_meta=query_meta, limit=3))

            for query_meta in dynamic_queries or []:
                if query_meta.get("target_area") != area_key:
                    continue
                encoded_query = urllib.parse.quote(query_meta["query"])
                url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
                items.extend(self._fetch_rss_feed(url, area_key=area_key, query_meta=query_meta, limit=2))

            deduped = []
            seen = set()
            for item in items:
                key = (item["headline"].lower(), item["source_name"].lower())
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(item)

            area_digest[area_key] = deduped[:6]
        return area_digest

    def _fetch_weather(self, city: str = "") -> str:
        try:
            url = f"https://wttr.in/{city}?format=3"
            request = urllib.request.Request(url, headers={"User-Agent": "curl/7.64.1"})
            with urllib.request.urlopen(request, timeout=5) as response:
                weather_data = response.read().decode("utf-8").strip()

            if weather_data and not weather_data.startswith("Unknown"):
                return weather_data
            return "Condicoes climaticas locais desconhecidas."
        except Exception as exc:
            logger.warning("World Consciousness: erro ao buscar clima: %s", exc)
            return "Atmosfera fisica externa nao detectada."

    def _detect_theme(self, area_key: str, text: str) -> Dict:
        normalized = (text or "").lower()
        themes = AREA_CONFIG[area_key]["themes"]
        scored = []
        for theme_key, config in themes.items():
            score = sum(1 for keyword in config["keywords"] if keyword in normalized)
            if score > 0:
                scored.append((theme_key, score))

        if not scored:
            fallback_key = next(iter(themes))
            fallback = themes[fallback_key]
            return {
                "theme_key": fallback_key,
                "theme_label": fallback["label"],
                "theme_score": 0,
                "reading": fallback["reading"],
            }

        theme_key, score = max(scored, key=lambda item: item[1])
        config = themes[theme_key]
        return {
            "theme_key": theme_key,
            "theme_label": config["label"],
            "theme_score": score,
            "reading": config["reading"],
        }

    def _recency_weight(self, published_at: str) -> float:
        try:
            published = datetime.fromisoformat(published_at)
            age_hours = max(0.0, (datetime.utcnow() - published.replace(tzinfo=None)).total_seconds() / 3600.0)
        except Exception:
            age_hours = 12.0

        if age_hours <= 6:
            return 1.0
        if age_hours <= 24:
            return 0.92
        if age_hours <= 72:
            return 0.82
        return 0.7

    def _normalize_signal(self, area_key: str, item: Dict, will_state: Optional[Dict] = None) -> Dict:
        theme = self._detect_theme(area_key, item["headline"])
        reputation = self._reputation_for(item["source_name"], item["source_domain"], item["source_class"])
        recency = self._recency_weight(item["published_at"])
        theme_bonus = min(0.14, 0.05 * max(0, theme["theme_score"]))
        will_bias = self._area_bias_for(area_key, will_state or {})
        query_origin = item.get("query_origin", "fixed_area_query")
        gap_bonus = 0.08 if query_origin == "will_gap_query" else 0.0
        signal_strength = round(min(1.0, max(0.0, reputation * recency + theme_bonus + will_bias + gap_bonus)), 3)

        return {
            "area_key": area_key,
            "headline": item["headline"],
            "source_name": item["source_name"],
            "source_domain": item["source_domain"],
            "source_url": item["source_url"],
            "source_class": item["source_class"],
            "scope": item["scope"],
            "published_at": item["published_at"],
            "query_origin": query_origin,
            "knowledge_gap": item.get("knowledge_gap"),
            "theme_key": theme["theme_key"],
            "theme_label": theme["theme_label"],
            "reading": theme["reading"],
            "tension": AREA_CONFIG[area_key]["tension"],
            "reputation_weight": reputation,
            "recency_weight": recency,
            "will_bias": will_bias,
            "gap_bonus": gap_bonus,
            "signal_strength": signal_strength,
        }

    def _load_recent_history(self) -> List[Dict]:
        if not os.path.exists(self.history_file):
            return []

        items: List[Dict] = []
        try:
            with open(self.history_file, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning("World Consciousness: falha ao ler historico: %s", exc)
            return []

        return items[-self.max_history_entries :]

    def _append_history(self, snapshot: Dict) -> None:
        history = self._load_recent_history()
        history.append(
            {
                "cache_timestamp": snapshot.get("cache_timestamp"),
                "current_time": snapshot.get("current_time"),
                "atmosphere": snapshot.get("atmosphere"),
                "dominant_tensions": snapshot.get("dominant_tensions", []),
                "confidence_overall": snapshot.get("confidence_overall"),
                "knowledge_resolution_summary": snapshot.get("knowledge_resolution_summary"),
                "consensus_map": snapshot.get("consensus_map", {}),
                "divergence_map": snapshot.get("divergence_map", {}),
                "work_seeds": snapshot.get("work_seeds", [])[:4],
                "hobby_seeds": snapshot.get("hobby_seeds", [])[:4],
                "area_panels": {
                    key: {
                        "dominant_reading": value.get("dominant_reading"),
                        "confidence": value.get("confidence"),
                        "dominant_themes": value.get("dominant_themes", []),
                    }
                    for key, value in (snapshot.get("area_panels", {}) or {}).items()
                },
            }
        )
        history = history[-self.max_history_entries :]

        try:
            with open(self.history_file, "w", encoding="utf-8") as handle:
                for item in history:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("World Consciousness: erro ao salvar historico: %s", exc)

    def _merge_with_cached_areas(self, area_digest: Dict[str, List[Dict]], cached_data: Dict) -> Dict[str, List[Dict]]:
        merged: Dict[str, List[Dict]] = {}
        cached_digest = (cached_data or {}).get("raw_area_digest", {}) or {}

        for area_key in AREA_CONFIG.keys():
            fresh = area_digest.get(area_key, []) or []
            merged[area_key] = fresh or (cached_digest.get(area_key, []) or [])

        return merged

    def _collect_stale_areas(self, original_area_digest: Dict[str, List[Dict]], merged_area_digest: Dict[str, List[Dict]]) -> List[str]:
        stale_areas = []
        for area_key in AREA_CONFIG.keys():
            had_fresh = bool(original_area_digest.get(area_key))
            has_merged = bool(merged_area_digest.get(area_key))
            if not had_fresh and has_merged:
                stale_areas.append(area_key)
        return stale_areas

    def _build_signals(self, area_digest: Dict[str, List[Dict]], will_state: Optional[Dict] = None) -> List[Dict]:
        signals: List[Dict] = []
        for area_key, items in area_digest.items():
            for item in items:
                if item.get("headline"):
                    signals.append(self._normalize_signal(area_key, item, will_state=will_state))
        return signals

    def _build_source_trace(self, signals: List[Dict]) -> Dict[str, List[Dict]]:
        grouped: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        for signal in signals:
            area_key = signal["area_key"]
            source_key = signal["source_name"].lower()
            existing = grouped[area_key].get(source_key)
            if not existing:
                grouped[area_key][source_key] = {
                    "source_name": signal["source_name"],
                    "source_domain": signal["source_domain"],
                    "source_class": signal["source_class"],
                    "scope": signal["scope"],
                    "reputation_weight": signal["reputation_weight"],
                    "signal_count": 1,
                    "total_strength": signal["signal_strength"],
                }
            else:
                existing["signal_count"] += 1
                existing["total_strength"] = round(existing["total_strength"] + signal["signal_strength"], 3)
                existing["reputation_weight"] = max(existing["reputation_weight"], signal["reputation_weight"])

        output = {}
        for area_key, sources in grouped.items():
            output[area_key] = sorted(
                sources.values(),
                key=lambda item: (item["total_strength"], item["signal_count"]),
                reverse=True,
            )[:6]
        return output

    def _build_area_panel(self, area_key: str, signals: List[Dict], stale: bool, history: List[Dict], attention_profile: Optional[Dict] = None) -> Dict:
        label = AREA_CONFIG[area_key]["label"]
        if not signals:
            return {
                "area_key": area_key,
                "label": label,
                "dominant_reading": "Sem leitura confiavel nesta janela.",
                "confidence": 0.14,
                "consensus_signals": [],
                "divergent_signals": [],
                "supporting_sources": [],
                "stale": stale,
                "seed_candidates": {"work": [], "hobby": []},
                "dominant_themes": [],
                "signal_count": 0,
                "scope_balance": {"brasil": 0, "mundo": 0},
            }

        total_strength = sum(signal["signal_strength"] for signal in signals)
        theme_strength = Counter()
        scope_balance = Counter()
        source_names = set()
        for signal in signals:
            theme_strength[signal["theme_label"]] += signal["signal_strength"]
            scope_balance[signal["scope"]] += 1
            source_names.add(signal["source_name"].lower())

        dominant_theme, dominant_weight = theme_strength.most_common(1)[0]
        convergence = dominant_weight / total_strength if total_strength else 0.0
        source_diversity = min(1.0, len(source_names) / 4.0)
        strength_score = min(1.0, total_strength / 4.5)
        confidence = round(min(0.98, (0.34 * strength_score) + (0.33 * source_diversity) + (0.33 * convergence)), 3)

        consensus_signals = []
        divergent_signals = []
        for theme_label, weight in theme_strength.most_common():
            ratio = weight / max(dominant_weight, 0.001)
            bucket = {"theme": theme_label, "weight": round(weight, 3), "share": round(weight / total_strength, 3)}
            if ratio >= 0.58 and len(consensus_signals) < 3:
                consensus_signals.append(bucket)
            elif ratio >= 0.28 and len(divergent_signals) < 3:
                divergent_signals.append(bucket)

        dominant_themes = [item["theme"] for item in consensus_signals] or [dominant_theme]
        top_readings = []
        for signal in signals:
            if signal["theme_label"] in dominant_themes and signal["reading"] not in top_readings:
                top_readings.append(signal["reading"])
            if len(top_readings) >= 2:
                break

        if len(top_readings) >= 2:
            dominant_reading = f"{top_readings[0]}; em paralelo, {top_readings[1]}."
        else:
            dominant_reading = top_readings[0] if top_readings else f"{label} em aberto e com pouco assentamento."

        supporting_sources = []
        source_trace = self._build_source_trace(signals).get(area_key, [])
        for item in source_trace[:4]:
            supporting_sources.append(
                {
                    "source_name": item["source_name"],
                    "source_class": item["source_class"],
                    "reputation_weight": item["reputation_weight"],
                    "signal_count": item["signal_count"],
                }
            )

        work_seeds = []
        hobby_seeds = []
        area_bias = ((attention_profile or {}).get("area_bias", {}) or {}).get(area_key, 0.0)
        for theme_name in dominant_themes[:2]:
            work_seed = f"{label}: produzir leitura/acao sobre {theme_name}."
            hobby_seed = f"{label}: explorar imagens, simbolos ou atmosferas ligados a {theme_name}."
            if area_bias > 0.05:
                work_seed = work_seed[:-1] + " Este eixo esta mais carregado pelas vontades do ciclo."
                hobby_seed = hobby_seed[:-1] + " Este eixo esta mais carregado pelas vontades do ciclo."
            work_seeds.append(work_seed)
            hobby_seeds.append(hobby_seed)

        previous_panel = {}
        if history:
            previous_panel = (history[-1].get("area_panels", {}) or {}).get(area_key, {})
        if previous_panel.get("dominant_themes") == dominant_themes[: len(previous_panel.get("dominant_themes", []))]:
            work_seeds = [seed + " Ha continuidade perceptivel nesta area." for seed in work_seeds]

        return {
            "area_key": area_key,
            "label": label,
            "dominant_reading": dominant_reading,
            "confidence": confidence,
            "consensus_signals": consensus_signals,
            "divergent_signals": divergent_signals,
            "supporting_sources": supporting_sources,
            "stale": stale,
            "seed_candidates": {"work": work_seeds[:3], "hobby": hobby_seeds[:3]},
            "dominant_themes": dominant_themes,
            "signal_count": len(signals),
            "scope_balance": {"brasil": scope_balance.get("brasil", 0), "mundo": scope_balance.get("mundo", 0)},
            "attention_bias": area_bias,
        }

    def _build_area_panels(self, signals: List[Dict], stale_areas: List[str], history: List[Dict], attention_profile: Optional[Dict] = None) -> Dict[str, Dict]:
        grouped = defaultdict(list)
        for signal in signals:
            grouped[signal["area_key"]].append(signal)

        panels = {}
        for area_key in AREA_CONFIG.keys():
            panels[area_key] = self._build_area_panel(area_key, grouped.get(area_key, []), area_key in stale_areas, history, attention_profile=attention_profile)
        return panels

    def _derive_atmosphere(self, area_panels: Dict[str, Dict]) -> str:
        ordered = sorted(area_panels.values(), key=lambda panel: panel.get("confidence", 0.0), reverse=True)
        strong = [panel for panel in ordered if panel.get("signal_count", 0) > 0][:2]
        if not strong:
            return "o horizonte externo carrega ruido difuso e uma abertura ainda sem forma"

        labels = [panel["label"].lower() for panel in strong]
        return f"o horizonte externo carrega pressao de {labels[0]} e reverberacao em {labels[-1]}"

    def _derive_dominant_tensions(self, area_panels: Dict[str, Dict]) -> List[str]:
        weighted = Counter()
        for area_key, panel in area_panels.items():
            if panel.get("signal_count", 0) <= 0:
                continue
            weighted[AREA_CONFIG[area_key]["tension"]] += float(panel.get("confidence", 0.0))
        return [item[0] for item in weighted.most_common(3)] or ["abertura e incerteza"]

    def _derive_overall_confidence(self, area_panels: Dict[str, Dict]) -> float:
        populated = [panel.get("confidence", 0.0) for panel in area_panels.values() if panel.get("signal_count", 0) > 0]
        if not populated:
            return 0.18
        return round(sum(populated) / len(populated), 3)

    def _derive_lucidity_level(self, confidence_overall: float) -> str:
        if confidence_overall >= 0.76:
            return "alta"
        if confidence_overall >= 0.54:
            return "media"
        return "baixa"

    def _build_consensus_map(self, area_panels: Dict[str, Dict]) -> Dict[str, List[str]]:
        return {
            area_key: [item["theme"] for item in panel.get("consensus_signals", [])]
            for area_key, panel in area_panels.items()
            if panel.get("consensus_signals")
        }

    def _build_divergence_map(self, area_panels: Dict[str, Dict]) -> Dict[str, List[str]]:
        return {
            area_key: [item["theme"] for item in panel.get("divergent_signals", [])]
            for area_key, panel in area_panels.items()
            if panel.get("divergent_signals")
        }

    def _build_confidence_map(self, area_panels: Dict[str, Dict]) -> Dict[str, float]:
        return {area_key: panel.get("confidence", 0.0) for area_key, panel in area_panels.items()}

    def _build_continuity(self, history: List[Dict], area_panels: Dict[str, Dict]) -> Dict:
        if not history:
            return {
                "persisting": [],
                "shifting": [],
                "weakening": [],
                "note": "Esta e uma percepcao ainda sem memoria acumulada do mundo.",
            }

        previous_panels = history[-1].get("area_panels", {}) or {}
        persisting = []
        shifting = []
        weakening = []

        for area_key, panel in area_panels.items():
            previous = previous_panels.get(area_key, {}) or {}
            previous_themes = previous.get("dominant_themes", []) or []
            current_themes = panel.get("dominant_themes", []) or []
            previous_confidence = float(previous.get("confidence", 0.0) or 0.0)
            current_confidence = float(panel.get("confidence", 0.0) or 0.0)

            if previous_themes and current_themes and current_themes[0] == previous_themes[0]:
                persisting.append(AREA_CONFIG[area_key]["label"])
            elif previous_themes and current_themes and current_themes[0] != previous_themes[0]:
                shifting.append(AREA_CONFIG[area_key]["label"])

            if previous_confidence - current_confidence >= 0.14:
                weakening.append(AREA_CONFIG[area_key]["label"])

        if persisting:
            note = "Persistem eixos do tempo em " + ", ".join(persisting[:3]) + "."
        elif shifting:
            note = "Houve deslocamento perceptivel em " + ", ".join(shifting[:3]) + "."
        else:
            note = "O mundo mudou pouco, mas ainda sem recorrencia forte entre leituras."

        if weakening:
            note += " Alguns sinais perderam firmeza em " + ", ".join(weakening[:2]) + "."

        return {"persisting": persisting, "shifting": shifting, "weakening": weakening, "note": note}

    def _build_world_seeds(self, area_panels: Dict[str, Dict]) -> Dict[str, List[str]]:
        work_seeds = []
        hobby_seeds = []
        ordered = sorted(
            area_panels.values(),
            key=lambda panel: (panel.get("confidence", 0.0), panel.get("attention_bias", 0.0)),
            reverse=True,
        )

        for panel in ordered:
            for seed in panel.get("seed_candidates", {}).get("work", []):
                if seed not in work_seeds:
                    work_seeds.append(seed)
            for seed in panel.get("seed_candidates", {}).get("hobby", []):
                if seed not in hobby_seeds:
                    hobby_seeds.append(seed)

        return {"work": work_seeds[:6], "hobby": hobby_seeds[:6]}

    def _flatten_headlines(self, signals: List[Dict]) -> List[str]:
        items = []
        seen = set()
        for signal in sorted(signals, key=lambda item: item["signal_strength"], reverse=True):
            summary = f"{signal['headline']} - {signal['source_name']}"
            key = summary.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(summary)
            if len(items) >= 14:
                break
        return items

    def _render_area_digest(self, area_panels: Dict[str, Dict]) -> Dict[str, List[str]]:
        area_digest = {}
        for area_key, panel in area_panels.items():
            lines = [panel.get("dominant_reading", "Sem leitura confiavel nesta janela.")]
            for signal in panel.get("consensus_signals", [])[:2]:
                lines.append(signal["theme"])
            area_digest[area_key] = lines
        return area_digest

    def _deterministic_lucidity_summary(self, area_panels: Dict[str, Dict], dominant_tensions: List[str], confidence_overall: float, continuity: Dict) -> Dict:
        consensus_areas = [
            panel["label"]
            for panel in area_panels.values()
            if len(panel.get("consensus_signals", [])) >= 2 and panel.get("confidence", 0.0) >= 0.58
        ]
        divergence_areas = [panel["label"] for panel in area_panels.values() if panel.get("divergent_signals")]

        zeitgeist = f"O tempo atual combina {', '.join(dominant_tensions[:2])} com lucidez {self._derive_lucidity_level(confidence_overall)}."
        mean_of_truth = (
            "A media da verdade aqui nao e certeza absoluta, mas convergencia ponderada entre sinais, "
            "fontes e repeticoes do momento."
        )
        admin_summary = (
            f"Areas em consenso: {', '.join(consensus_areas[:4]) or 'nenhuma ainda'}. "
            f"Areas em disputa: {', '.join(divergence_areas[:4]) or 'baixa disputa explicita'}. "
            f"{continuity.get('note', '')}"
        ).strip()
        prompt_summary = (
            f"Atmosfera do Tempo: {self._derive_atmosphere(area_panels)}\n"
            f"Tensoes Centrais: {', '.join(dominant_tensions[:3])}\n"
            f"Areas em consenso: {', '.join(consensus_areas[:4]) or 'nenhuma forte nesta janela'}\n"
            f"Areas em disputa: {', '.join(divergence_areas[:4]) or 'sem disputa forte detectada'}\n"
            f"Nivel geral de lucidez: {self._derive_lucidity_level(confidence_overall)} ({confidence_overall:.2f})"
        )

        return {
            "zeitgeist": zeitgeist,
            "mean_of_truth": mean_of_truth,
            "admin_summary": admin_summary,
            "prompt_summary": prompt_summary,
        }

    def _llm_enrich_lucidity(self, snapshot: Dict) -> Dict:
        try:
            from llm_providers import get_llm_response
        except Exception:
            return {}

        compact_panels = []
        for area_key, panel in (snapshot.get("area_panels", {}) or {}).items():
            compact_panels.append(
                {
                    "area": AREA_CONFIG[area_key]["label"],
                    "dominant_reading": panel.get("dominant_reading"),
                    "confidence": panel.get("confidence"),
                    "consensus": panel.get("consensus_signals", [])[:2],
                    "divergence": panel.get("divergent_signals", [])[:2],
                }
            )

        prompt = f"""
Voce esta sintetizando lucidez do tempo para um agente.
Nao invente fatos novos. Trabalhe apenas com o estado estruturado abaixo.
Sua tarefa e produzir uma leitura breve do zeitgeist, como uma media da verdade:
nao uma verdade absoluta, mas uma leitura lucida baseada em consensos e divergencias.

Devolva APENAS JSON valido com as chaves:
- zeitgeist
- mean_of_truth
- admin_summary
- prompt_summary

Estado estruturado:
{json.dumps({
    "atmosphere": snapshot.get("atmosphere"),
    "dominant_tensions": snapshot.get("dominant_tensions"),
    "confidence_overall": snapshot.get("confidence_overall"),
    "consensus_map": snapshot.get("consensus_map"),
    "divergence_map": snapshot.get("divergence_map"),
    "continuity_note": snapshot.get("continuity_note"),
    "areas": compact_panels,
}, ensure_ascii=False)}
"""
        try:
            raw = get_llm_response(prompt, temperature=0.35, max_tokens=700)
            parsed = self._extract_json_object(raw)
            return parsed or {}
        except Exception as exc:
            logger.warning("World Consciousness: sintese LLM indisponivel, usando fallback deterministico: %s", exc)
            return {}

    def _format_prompt_summary(self, snapshot: Dict) -> str:
        work_seeds = snapshot.get("work_seeds", []) or []
        hobby_seeds = snapshot.get("hobby_seeds", []) or []
        consensus_areas = [AREA_CONFIG[key]["label"] for key in (snapshot.get("consensus_map", {}) or {}).keys()]
        divergence_areas = [AREA_CONFIG[key]["label"] for key in (snapshot.get("divergence_map", {}) or {}).keys()]
        area_panels = snapshot.get("area_panels", {}) or {}
        loaded_panels = sorted(
            [
                panel
                for panel in area_panels.values()
                if panel.get("signal_count", 0) > 0
            ],
            key=lambda panel: (panel.get("confidence", 0.0), panel.get("signal_count", 0)),
            reverse=True,
        )

        def _compact_reading(text: str, limit: int = 116) -> str:
            cleaned = (text or "").strip().rstrip(".")
            if not cleaned:
                return "sem leitura confiavel nesta janela"
            cleaned = cleaned.replace("; em paralelo, ", "; ")
            if len(cleaned) <= limit:
                return cleaned
            clipped = cleaned[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
            return (clipped or cleaned[:limit]).rstrip(".") + "..."

        def _derive_human_implication() -> str:
            tension_map = {
                "cuidado e risco": "o presente pressiona cuidado, vulnerabilidade e decisoes mais prudentes",
                "descoberta e limite": "o presente mistura curiosidade, possibilidade e freio",
                "criacao e deslocamento": "o presente pede adaptacao, reposicionamento e algum desapego",
                "controle e exposicao": "o presente amplia vigilancia, visibilidade e cautela no que se revela",
                "expressao e mercantilizacao": "o presente tensiona autenticidade, exibicao e captura do que deveria ser vivo",
                "cuidado e risco, descoberta e limite": "o presente mistura zelo, curiosidade e freios mais duros",
            }

            dominant_tensions = snapshot.get("dominant_tensions", [])[:2]
            implications = []
            for tension in dominant_tensions:
                mapped = tension_map.get(tension)
                if mapped and mapped not in implications:
                    implications.append(mapped)

            if not implications and loaded_panels:
                top_labels = [panel["label"].lower() for panel in loaded_panels[:2]]
                return f"o presente pesa mais sobre {', '.join(top_labels)} e tende a reorganizar a sensacao coletiva de prioridade"

            if len(implications) >= 2:
                return f"{implications[0]}; ao mesmo tempo, {implications[1]}"
            if implications:
                return implications[0]
            return "o presente carrega pressao difusa, mas ainda sem um eixo humano nitido"

        dominant_area_labels = [panel["label"] for panel in loaded_panels[:3]]
        area_reading_lines = [
            f"- {panel['label']}: {_compact_reading(panel.get('dominant_reading', ''))}."
            for panel in loaded_panels[:3]
        ]
        human_implication = _derive_human_implication()

        lines = [
            "[CONSCIENCIA DA ATUALIDADE (Lucidez do Tempo)]",
            f"Data/Hora Local: {snapshot.get('current_time', 'indisponivel')}",
            f"Clima Fisico: {snapshot.get('weather', 'indisponivel')}",
            "",
            f"Atmosfera do Tempo: {snapshot.get('atmosphere', 'o horizonte externo ainda nao ganhou forma suficiente')}",
            f"Tensoes Centrais: {', '.join(snapshot.get('dominant_tensions', [])[:3]) or 'abertura e incerteza'}",
            f"Areas em consenso: {', '.join(consensus_areas[:4]) or 'nenhuma forte nesta janela'}",
            f"Areas em disputa: {', '.join(divergence_areas[:4]) or 'sem disputa forte detectada'}",
            f"Nivel geral de lucidez: {snapshot.get('lucidity_level', 'media')} ({snapshot.get('confidence_overall', 0.0):.2f})",
            f"Areas mais carregadas agora: {', '.join(dominant_area_labels) or 'nenhuma com peso suficiente nesta janela'}",
            f"Implicacao humana do tempo: {human_implication}",
            f"Vies atual das vontades: {snapshot.get('will_bias_summary', 'sem vies ativo')}",
            f"Elaboracao do saber: {snapshot.get('knowledge_resolution_summary', 'sem aprofundamento epistemico especial')}",
            f"Continuidade Percebida: {snapshot.get('continuity_note', 'sem memoria acumulada do mundo nesta janela')}",
            "",
            "Leituras dominantes do momento:",
        ]
        if area_reading_lines:
            lines.extend(area_reading_lines)
        else:
            lines.append("- Sem leitura forte por area nesta janela.")

        lines.extend([
            "",
            "Seeds ativos para acao:",
        ])
        if work_seeds:
            for seed in work_seeds[:3]:
                lines.append(f"- {seed}")
        else:
            lines.append("- Nenhum seed de acao consolidado nesta janela.")

        lines.extend(["", "Seeds ativos para hobby/arte:"])
        if hobby_seeds:
            for seed in hobby_seeds[:3]:
                lines.append(f"- {seed}")
        else:
            lines.append("- Nenhum seed simbolico consolidado nesta janela.")

        return "\n".join(lines)

    def _format_admin_summary(self, snapshot: Dict) -> str:
        lines = [
            "[PAINEL DE LUCIDEZ DO MUNDO]",
            f"Leitura forte: {snapshot.get('world_lucidity_summary', {}).get('zeitgeist', '')}",
            f"Media da verdade: {snapshot.get('world_lucidity_summary', {}).get('mean_of_truth', '')}",
            f"Confianca geral: {snapshot.get('confidence_overall', 0.0):.2f} ({snapshot.get('lucidity_level', 'media')})",
            f"Vies das vontades: {snapshot.get('will_bias_summary', 'sem vies ativo')}",
            f"Leitura do saber: {snapshot.get('knowledge_resolution_summary', 'sem aprofundamento epistemico especial')}",
            f"Continuidade: {snapshot.get('continuity_note', '')}",
            "",
            "Areas nucleares:",
        ]

        for panel in (snapshot.get("area_panels", {}) or {}).values():
            lines.append(
                f"- {panel['label']}: {panel.get('dominant_reading', '')} "
                f"[conf={panel.get('confidence', 0.0):.2f}; consenso={', '.join(item['theme'] for item in panel.get('consensus_signals', [])[:2]) or 'nenhum'}; "
                f"divergencia={', '.join(item['theme'] for item in panel.get('divergent_signals', [])[:2]) or 'baixa'}]"
            )

        lines.extend(["", "Seeds para Work/Action:"])
        for seed in (snapshot.get("work_seeds", []) or [])[:5]:
            lines.append(f"- {seed}")

        lines.extend(["", "Seeds para Hobby/Art:"])
        for seed in (snapshot.get("hobby_seeds", []) or [])[:5]:
            lines.append(f"- {seed}")

        return "\n".join(lines)

    def _build_world_state(
        self,
        locale: str = "",
        cached_data: Dict = None,
        will_state: Optional[Dict] = None,
        epistemic_trigger: Optional[str] = None,
    ) -> Dict:
        now = datetime.now()
        resolved_will_state = self._resolve_will_state(will_state)
        attention_profile = self._build_attention_profile(resolved_will_state)
        epistemic_active = self._should_activate_epistemic_discernment(
            resolved_will_state,
            epistemic_trigger=epistemic_trigger,
        )
        knowledge_gap: Dict[str, Any] = {}
        knowledge_probe: Dict[str, Any] = {}
        dynamic_queries: List[Dict[str, Any]] = []
        if epistemic_active:
            knowledge_gap = self._formulate_knowledge_gap(
                resolved_will_state,
                cached_data or {},
                epistemic_trigger=epistemic_trigger,
            )
            knowledge_probe = self._probe_knowledge_source(knowledge_gap, resolved_will_state, cached_data or {})
            if knowledge_probe.get("knowledge_source_decision") == "web_required":
                dynamic_queries = self._build_dynamic_queries(knowledge_gap, knowledge_probe)

        raw_area_digest = self._fetch_area_news(dynamic_queries=dynamic_queries)
        area_items = self._merge_with_cached_areas(raw_area_digest, cached_data or {})
        stale_areas = self._collect_stale_areas(raw_area_digest, area_items)
        signals = self._build_signals(area_items, will_state=resolved_will_state)
        history = self._load_recent_history()
        area_panels = self._build_area_panels(signals, stale_areas, history, attention_profile=attention_profile)
        source_trace = self._build_source_trace(signals)
        consensus_map = self._build_consensus_map(area_panels)
        divergence_map = self._build_divergence_map(area_panels)
        confidence_map = self._build_confidence_map(area_panels)
        atmosphere = self._derive_atmosphere(area_panels)
        dominant_tensions = self._derive_dominant_tensions(area_panels)
        confidence_overall = self._derive_overall_confidence(area_panels)
        lucidity_level = self._derive_lucidity_level(confidence_overall)
        continuity = self._build_continuity(history, area_panels)
        world_seeds = self._build_world_seeds(area_panels)
        if knowledge_probe.get("knowledge_seed"):
            world_seeds["work"] = [knowledge_probe["knowledge_seed"], *world_seeds["work"]][:6]
        weather_text = self._fetch_weather("Sao_Paulo" if not locale else locale)
        headlines = self._flatten_headlines(signals)
        knowledge_decision = knowledge_probe.get("knowledge_source_decision") or "inactive"
        if knowledge_decision == "latent_sufficient":
            knowledge_summary = "o saber deste ciclo foi trabalhado sobretudo por elaboracao interna do que o modelo ja podia oferecer"
        elif knowledge_decision == "web_required":
            knowledge_summary = "o saber deste ciclo precisou de atualizacao externa para ganhar forma mais justa"
        elif knowledge_decision == "already_integrated":
            knowledge_summary = "o saber deste ciclo apareceu mais como reintegracao do que ja vinha sendo metabolizado"
        else:
            knowledge_summary = "o saber seguiu a leitura ampla do mundo, sem aprofundamento epistemico especial"

        world_state = {
            "state_version": WORLD_STATE_VERSION,
            "cache_timestamp": now.isoformat(),
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weather": weather_text,
            "signals": signals,
            "headlines": headlines,
            "raw_area_digest": area_items,
            "area_digest": self._render_area_digest(area_panels),
            "stale_areas": stale_areas,
            "atmosphere": atmosphere,
            "dominant_tension": dominant_tensions[0] if dominant_tensions else "abertura e incerteza",
            "dominant_tensions": dominant_tensions,
            "confidence_overall": confidence_overall,
            "lucidity_level": lucidity_level,
            "area_panels": area_panels,
            "world_area_panels": area_panels,
            "consensus_map": consensus_map,
            "world_consensus_map": consensus_map,
            "divergence_map": divergence_map,
            "world_divergence_map": divergence_map,
            "world_confidence_map": confidence_map,
            "source_trace": source_trace,
            "world_source_trace": source_trace,
            "continuity": continuity,
            "continuity_note": continuity["note"],
            "will_signature": self._will_signature(resolved_will_state),
            "attention_profile": attention_profile,
            "will_bias_summary": self._summarize_will_bias(resolved_will_state, attention_profile),
            "biased_area_order": attention_profile.get("biased_area_order", []),
            "seed_bias_explanation": self._summarize_will_bias(resolved_will_state, attention_profile),
            "epistemic_discernment_active": epistemic_active,
            "knowledge_gap": knowledge_gap,
            "knowledge_source_decision": knowledge_decision,
            "latent_probe_summary": knowledge_probe.get("latent_probe_summary"),
            "dynamic_queries": dynamic_queries,
            "knowledge_findings": knowledge_probe.get("knowledge_findings"),
            "knowledge_seed": knowledge_probe.get("knowledge_seed"),
            "knowledge_resolution_summary": knowledge_summary,
            "world_seeds": world_seeds,
            "work_seeds": world_seeds["work"],
            "hobby_seeds": world_seeds["hobby"],
        }

        lucidity_summary = self._deterministic_lucidity_summary(area_panels, dominant_tensions, confidence_overall, continuity)
        llm_summary = self._llm_enrich_lucidity(world_state)
        world_state["world_lucidity_summary"] = {
            "zeitgeist": (llm_summary.get("zeitgeist") or lucidity_summary["zeitgeist"]).strip(),
            "mean_of_truth": (llm_summary.get("mean_of_truth") or lucidity_summary["mean_of_truth"]).strip(),
            "admin_summary": (llm_summary.get("admin_summary") or lucidity_summary["admin_summary"]).strip(),
            "prompt_summary": (llm_summary.get("prompt_summary") or lucidity_summary["prompt_summary"]).strip(),
        }
        world_state["formatted_prompt_summary"] = self._format_prompt_summary(world_state)
        world_state["formatted_admin_summary"] = self._format_admin_summary(world_state)
        world_state["formatted_synthesis"] = world_state["formatted_prompt_summary"]
        world_state["news"] = "\n".join(f"- {headline}" for headline in headlines) if headlines else "Sem headlines consolidadas."
        return world_state

    def _save_cache(self, world_state: Dict) -> None:
        try:
            with open(self.cache_file, "w", encoding="utf-8") as handle:
                json.dump(world_state, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("World Consciousness: erro ao salvar cache: %s", exc)

    def _load_cache(self) -> Dict:
        if not os.path.exists(self.cache_file):
            return {}

        try:
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            logger.warning("World Consciousness: falha ao ler cache: %s", exc)
            return {}

    def get_history(self, limit: int = 12) -> List[Dict]:
        history = self._load_recent_history()
        return history[-limit:]

    def get_world_state(
        self,
        force_refresh: bool = False,
        locale: str = "",
        will_state: Optional[Dict] = None,
        epistemic_trigger: Optional[str] = None,
    ) -> Dict:
        now = datetime.now()
        resolved_will_state = self._resolve_will_state(will_state)
        will_signature = self._will_signature(resolved_will_state)
        cached_data = self._load_cache()
        cache_version = int(cached_data.get("state_version", 0) or 0) if cached_data else 0

        if not force_refresh and cached_data and cache_version >= WORLD_STATE_VERSION:
            cached_time = datetime.fromisoformat(cached_data.get("cache_timestamp", "2000-01-01T00:00:00"))
            if now - cached_time < timedelta(hours=self.cache_duration_hours) and cached_data.get("will_signature") == will_signature:
                logger.info("World Consciousness: carregando estado do mundo via cache.")
                cached_data["current_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                cached_data["formatted_prompt_summary"] = self._format_prompt_summary(cached_data)
                cached_data["formatted_admin_summary"] = self._format_admin_summary(cached_data)
                cached_data["formatted_synthesis"] = cached_data["formatted_prompt_summary"]
                return cached_data

        logger.info("World Consciousness: buscando novo estado do mundo nas redes externas...")
        world_state = self._build_world_state(
            locale=locale,
            cached_data=cached_data,
            will_state=resolved_will_state,
            epistemic_trigger=epistemic_trigger,
        )
        self._save_cache(world_state)
        self._append_history(world_state)
        return world_state


world_consciousness = WorldConsciousnessFetcher()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testando modulo World Consciousness...")
    state = world_consciousness.get_world_state(force_refresh=True)
    print("\nRESUMO DE PROMPT:\n")
    print(state["formatted_prompt_summary"])
