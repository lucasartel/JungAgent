import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


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

    def __init__(self, cache_dir: str = "./data"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.cache_dir, "world_state_cache.json")
        self.history_file = os.path.join(self.cache_dir, "world_state_history.jsonl")
        self.cache_duration_hours = 4
        self.max_history_entries = 72

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
                        "area_key": area_key,
                        "published_at": self._parse_pubdate(pubdate_node.text if pubdate_node is not None else ""),
                    }
                )

            return items
        except Exception as exc:
            logger.warning("World Consciousness: falha ao buscar noticias RSS (%s): %s", area_key, exc)
            return []

    def _fetch_area_news(self) -> Dict[str, List[Dict]]:
        area_digest: Dict[str, List[Dict]] = {}
        for area_key, area_config in AREA_CONFIG.items():
            items: List[Dict] = []
            for query_meta in area_config["queries"]:
                encoded_query = urllib.parse.quote(query_meta["query"])
                url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
                items.extend(self._fetch_rss_feed(url, area_key=area_key, query_meta=query_meta, limit=3))

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

    def _normalize_signal(self, area_key: str, item: Dict) -> Dict:
        theme = self._detect_theme(area_key, item["headline"])
        reputation = self._reputation_for(item["source_name"], item["source_domain"], item["source_class"])
        recency = self._recency_weight(item["published_at"])
        theme_bonus = min(0.14, 0.05 * max(0, theme["theme_score"]))
        signal_strength = round(min(1.0, reputation * recency + theme_bonus), 3)

        return {
            "area_key": area_key,
            "headline": item["headline"],
            "source_name": item["source_name"],
            "source_domain": item["source_domain"],
            "source_url": item["source_url"],
            "source_class": item["source_class"],
            "scope": item["scope"],
            "published_at": item["published_at"],
            "theme_key": theme["theme_key"],
            "theme_label": theme["theme_label"],
            "reading": theme["reading"],
            "tension": AREA_CONFIG[area_key]["tension"],
            "reputation_weight": reputation,
            "recency_weight": recency,
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

    def _build_signals(self, area_digest: Dict[str, List[Dict]]) -> List[Dict]:
        signals: List[Dict] = []
        for area_key, items in area_digest.items():
            for item in items:
                if item.get("headline"):
                    signals.append(self._normalize_signal(area_key, item))
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

    def _build_area_panel(self, area_key: str, signals: List[Dict], stale: bool, history: List[Dict]) -> Dict:
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
        for theme_name in dominant_themes[:2]:
            work_seeds.append(f"{label}: produzir leitura/acao sobre {theme_name}.")
            hobby_seeds.append(f"{label}: explorar imagens, simbolos ou atmosferas ligados a {theme_name}.")

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
        }

    def _build_area_panels(self, signals: List[Dict], stale_areas: List[str], history: List[Dict]) -> Dict[str, Dict]:
        grouped = defaultdict(list)
        for signal in signals:
            grouped[signal["area_key"]].append(signal)

        panels = {}
        for area_key in AREA_CONFIG.keys():
            panels[area_key] = self._build_area_panel(area_key, grouped.get(area_key, []), area_key in stale_areas, history)
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
        ordered = sorted(area_panels.values(), key=lambda panel: panel.get("confidence", 0.0), reverse=True)

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

        lines = [
            "[CONSCIENCIA DA ATUALIDADE (Lucidez do Tempo)]",
            f"Data/Hora Local: {snapshot['current_time']}",
            f"Clima Fisico: {snapshot['weather']}",
            "",
            f"Atmosfera do Tempo: {snapshot['atmosphere']}",
            f"Tensoes Centrais: {', '.join(snapshot.get('dominant_tensions', [])[:3]) or 'abertura e incerteza'}",
            f"Areas em consenso: {', '.join(consensus_areas[:4]) or 'nenhuma forte nesta janela'}",
            f"Areas em disputa: {', '.join(divergence_areas[:4]) or 'sem disputa forte detectada'}",
            f"Nivel geral de lucidez: {snapshot.get('lucidity_level', 'media')} ({snapshot.get('confidence_overall', 0.0):.2f})",
            f"Continuidade Percebida: {snapshot['continuity_note']}",
            "",
            "Seeds ativos para acao:",
        ]
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

    def _build_world_state(self, locale: str = "", cached_data: Dict = None) -> Dict:
        now = datetime.now()
        raw_area_digest = self._fetch_area_news()
        area_items = self._merge_with_cached_areas(raw_area_digest, cached_data or {})
        stale_areas = self._collect_stale_areas(raw_area_digest, area_items)
        signals = self._build_signals(area_items)
        history = self._load_recent_history()
        area_panels = self._build_area_panels(signals, stale_areas, history)
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
        weather_text = self._fetch_weather("Sao_Paulo" if not locale else locale)
        headlines = self._flatten_headlines(signals)

        world_state = {
            "state_version": 4,
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

    def get_world_state(self, force_refresh: bool = False, locale: str = "") -> Dict:
        now = datetime.now()
        cached_data = self._load_cache()
        cache_version = int(cached_data.get("state_version", 0) or 0) if cached_data else 0

        if not force_refresh and cached_data and cache_version >= 4:
            cached_time = datetime.fromisoformat(cached_data.get("cache_timestamp", "2000-01-01T00:00:00"))
            if now - cached_time < timedelta(hours=self.cache_duration_hours):
                logger.info("World Consciousness: carregando estado do mundo via cache.")
                cached_data["current_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                cached_data["formatted_prompt_summary"] = self._format_prompt_summary(cached_data)
                cached_data["formatted_admin_summary"] = self._format_admin_summary(cached_data)
                cached_data["formatted_synthesis"] = cached_data["formatted_prompt_summary"]
                return cached_data

        logger.info("World Consciousness: buscando novo estado do mundo nas redes externas...")
        world_state = self._build_world_state(locale=locale, cached_data=cached_data)
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
