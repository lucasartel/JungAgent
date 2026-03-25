import json
import logging
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)


WORLD_CATEGORIES = {
    "geopolitica": (
        "guerra", "iran", "israel", "ucrania", "russia", "ataque", "bombardeio",
        "conflito", "missil", "otan", "china", "eua", "trump", "estreito",
    ),
    "politica_institucional": (
        "lula", "bolsonaro", "moraes", "stf", "governo", "congresso", "eleicao",
        "ministro", "presidente", "julgamento", "supremo", "prisao",
    ),
    "clima_natureza": (
        "chuva", "temporais", "ciclone", "calor", "frio", "seca", "enchente",
        "vento", "meteorologia", "clima", "tempestade", "incendio",
    ),
    "economia_trabalho": (
        "economia", "mercado", "empresa", "trabalho", "emprego", "dolar",
        "inflacao", "juros", "industria", "negocio", "salario",
    ),
    "tecnologia_cultura": (
        "ia", "inteligencia artificial", "tecnologia", "google", "meta", "openai",
        "filme", "livro", "arte", "cultura", "musica", "streaming",
    ),
    "saude_sociedade": (
        "saude", "hospital", "virus", "pandemia", "morte", "crime", "educacao",
        "familia", "crianca", "violencia", "sociedade",
    ),
}


CATEGORY_TENSIONS = {
    "geopolitica": "forca e vulnerabilidade",
    "politica_institucional": "ordem e ruptura",
    "clima_natureza": "controle e exposicao",
    "economia_trabalho": "seguranca e instabilidade",
    "tecnologia_cultura": "criacao e deslocamento",
    "saude_sociedade": "cuidado e risco",
    "indefinido": "abertura e incerteza",
}


CATEGORY_ATMOSPHERES = {
    "geopolitica": "o horizonte externo carrega conflito, estrategia e pressao",
    "politica_institucional": "o horizonte externo carrega disputa simbolica e instabilidade institucional",
    "clima_natureza": "o horizonte externo carrega vulnerabilidade material e mudanca de ambiente",
    "economia_trabalho": "o horizonte externo carrega pressao de sobrevivencia, ajuste e trabalho",
    "tecnologia_cultura": "o horizonte externo carrega aceleracao cultural e rearranjo tecnico",
    "saude_sociedade": "o horizonte externo carrega fragilidade humana, cuidado e exposicao social",
    "indefinido": "o horizonte externo carrega ruido difuso e uma abertura ainda sem forma",
}


WORLD_AREAS = {
    "politica": {
        "label": "Politica",
        "query": "politica mundial OR governo OR congresso OR eleicoes OR presidente",
        "fallback_category": "politica_institucional",
    },
    "economia": {
        "label": "Economia",
        "query": "economia global OR mercado OR inflacao OR juros OR emprego",
        "fallback_category": "economia_trabalho",
    },
    "tecnologia": {
        "label": "Tecnologia",
        "query": "tecnologia OR inteligencia artificial OR software OR big tech",
        "fallback_category": "tecnologia_cultura",
    },
    "entretenimento": {
        "label": "Entretenimento",
        "query": "entretenimento OR cinema OR musica OR streaming OR celebridades",
        "fallback_category": "tecnologia_cultura",
    },
    "ciencia": {
        "label": "Ciencia",
        "query": "ciencia OR descoberta OR pesquisa OR espaco OR saude",
        "fallback_category": "saude_sociedade",
    },
    "clima": {
        "label": "Clima e Meio Ambiente",
        "query": "clima OR meio ambiente OR calor OR chuva OR desastres naturais",
        "fallback_category": "clima_natureza",
    },
}


class WorldConsciousnessFetcher:
    """
    Fetches a lightweight world digest and turns it into a structured,
    multi-area state for prompt injection and future loop integration.
    """

    def __init__(self, cache_dir: str = "./data"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.cache_dir, "world_state_cache.json")
        self.history_file = os.path.join(self.cache_dir, "world_state_history.jsonl")
        self.cache_duration_hours = 4
        self.max_history_entries = 48

    def _fetch_rss_feed(self, url: str, limit: int = 3) -> List[str]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            headlines: List[str] = []
            for index, item in enumerate(root.findall("./channel/item")):
                if index >= limit:
                    break
                title = item.find("title")
                if title is not None and title.text:
                    headlines.append(title.text.strip())

            return headlines
        except Exception as exc:
            logger.error("World Consciousness: erro ao buscar noticias RSS: %s", exc)
            return []

    def _fetch_area_news(self) -> Dict[str, List[str]]:
        """Fetch a compact world digest split by broad public areas."""
        area_digest: Dict[str, List[str]] = {}

        for area_key, area_config in WORLD_AREAS.items():
            query = urllib.parse.quote(area_config["query"])
            url = f"https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            area_digest[area_key] = self._fetch_rss_feed(url, limit=2)

        return area_digest

    def _fetch_weather(self, city: str = "") -> str:
        try:
            url = f"https://wttr.in/{city}?format=3"
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.64.1"})
            with urllib.request.urlopen(req, timeout=5) as response:
                weather_data = response.read().decode("utf-8").strip()

            if weather_data and not weather_data.startswith("Unknown"):
                return weather_data
            return "Condicoes climaticas locais desconhecidas."
        except Exception as exc:
            logger.error("World Consciousness: erro ao buscar clima: %s", exc)
            return "Atmosfera fisica externa nao detectada."

    def _categorize_headline(self, headline: str, area_hint: str = "") -> Dict:
        normalized = (headline or "").lower()
        scores = {}
        for category, keywords in WORLD_CATEGORIES.items():
            score = sum(1 for keyword in keywords if keyword in normalized)
            if score > 0:
                scores[category] = score

        if not scores:
            hinted_category = WORLD_AREAS.get(area_hint, {}).get("fallback_category")
            if hinted_category:
                category = hinted_category
                score = 1
            else:
                category = "indefinido"
                score = 0
        else:
            category, score = max(scores.items(), key=lambda item: item[1])

        intensity = min(0.35 + (0.15 * score), 1.0) if category != "indefinido" else 0.25
        return {
            "headline": headline,
            "area": area_hint or "geral",
            "category": category,
            "intensity": round(intensity, 2),
            "tension": CATEGORY_TENSIONS.get(category, CATEGORY_TENSIONS["indefinido"]),
        }

    def _build_signal_map(self, area_digest: Dict[str, List[str]]) -> List[Dict]:
        signals: List[Dict] = []
        for area_key, headlines in area_digest.items():
            for headline in headlines:
                if headline:
                    signals.append(self._categorize_headline(headline, area_hint=area_key))
        return signals

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
                "dominant_category": snapshot.get("dominant_category"),
                "dominant_tension": snapshot.get("dominant_tension"),
                "area_digest": snapshot.get("area_digest", {}),
                "signals": snapshot.get("signals", [])[:4],
            }
        )
        history = history[-self.max_history_entries :]

        try:
            with open(self.history_file, "w", encoding="utf-8") as handle:
                for item in history:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("World Consciousness: erro ao salvar historico: %s", exc)

    def _derive_atmosphere(self, signals: List[Dict]) -> str:
        if not signals:
            return CATEGORY_ATMOSPHERES["indefinido"]

        categories = [signal["category"] for signal in signals]
        dominant_category, _ = Counter(categories).most_common(1)[0]
        return CATEGORY_ATMOSPHERES.get(dominant_category, CATEGORY_ATMOSPHERES["indefinido"])

    def _derive_dominant_tension(self, signals: List[Dict]) -> str:
        if not signals:
            return CATEGORY_TENSIONS["indefinido"]

        weighted = Counter()
        for signal in signals:
            weighted[signal["category"]] += float(signal.get("intensity", 0.0))

        dominant_category, _ = weighted.most_common(1)[0]
        return CATEGORY_TENSIONS.get(dominant_category, CATEGORY_TENSIONS["indefinido"])

    def _derive_continuity_note(self, history: List[Dict], current_category: str) -> str:
        if not history:
            return "Esta e uma percepcao ainda sem memoria acumulada do mundo."

        recent_categories = [item.get("dominant_category", "indefinido") for item in history[-6:]]
        if not recent_categories:
            return "O mundo aparece aqui como um sinal ainda pouco assentado."

        recurrence = recent_categories.count(current_category)
        if recurrence >= 3:
            return (
                "Nas ultimas leituras, o mundo tem retornado ao eixo de "
                f"{CATEGORY_TENSIONS.get(current_category, 'incerteza')}."
            )

        if recent_categories[-1] != current_category:
            previous = recent_categories[-1]
            previous_tension = CATEGORY_TENSIONS.get(previous, CATEGORY_TENSIONS["indefinido"])
            current_tension = CATEGORY_TENSIONS.get(current_category, CATEGORY_TENSIONS["indefinido"])
            return f"Houve deslocamento recente: de {previous_tension} para {current_tension}."

        return "O mundo ainda preserva a mesma pressao dominante da ultima leitura."

    def _format_headlines(self, headlines: List[str]) -> str:
        if not headlines:
            return "As correntes de informacao global estao momentaneamente inacessiveis."
        return "\n".join(f"- {headline}" for headline in headlines)

    def _format_area_digest(self, snapshot: Dict) -> List[str]:
        lines = ["Leituras por Area:"]
        area_digest = snapshot.get("area_digest", {}) or {}
        stale_areas = set(snapshot.get("stale_areas", []) or [])

        for area_key, area_config in WORLD_AREAS.items():
            label = area_config["label"]
            headlines = area_digest.get(area_key, []) or []
            stale_suffix = " (cache recente)" if area_key in stale_areas else ""

            if headlines:
                lines.append(f"- {label}{stale_suffix}: {headlines[0]}")
            else:
                lines.append(f"- {label}: sem leitura confiavel nesta janela.")

        return lines

    def _format_synthesis(self, snapshot: Dict) -> str:
        lines = [
            "[CONSCIENCIA DA ATUALIDADE (Curiosidade Ontologica)]",
            f"Data/Hora Local: {snapshot['current_time']}",
            f"Clima Fisico: {snapshot['weather']}",
            "",
            f"Atmosfera do Mundo: {snapshot['atmosphere']}",
            f"Tensao Dominante Agora: {snapshot['dominant_tension']}",
            f"Continuidade Percebida: {snapshot['continuity_note']}",
            "",
        ]

        lines.extend(self._format_area_digest(snapshot))
        lines.extend(
            [
                "",
                "Principais Ecos Globais (Noticias do Mundo Real):",
                self._format_headlines(snapshot["headlines"]),
                "",
            ]
        )

        stale_areas = snapshot.get("stale_areas", [])
        if stale_areas:
            stale_labels = [
                WORLD_AREAS[area_key]["label"]
                for area_key in stale_areas
                if area_key in WORLD_AREAS
            ]
            if stale_labels:
                lines.extend(
                    [
                        "Observacao de Consistencia:",
                        f"- Algumas areas usaram o cache mais recente para manter continuidade: {', '.join(stale_labels)}.",
                        "",
                    ]
                )

        return "\n".join(lines)

    def _merge_with_cached_areas(self, area_digest: Dict[str, List[str]], cached_data: Dict) -> Dict[str, List[str]]:
        merged: Dict[str, List[str]] = {}
        cached_digest = (cached_data or {}).get("area_digest", {}) or {}

        for area_key in WORLD_AREAS.keys():
            fresh = area_digest.get(area_key, []) or []
            if fresh:
                merged[area_key] = fresh
            else:
                merged[area_key] = cached_digest.get(area_key, []) or []

        return merged

    def _collect_stale_areas(
        self,
        original_area_digest: Dict[str, List[str]],
        merged_area_digest: Dict[str, List[str]],
    ) -> List[str]:
        stale_areas = []
        for area_key in WORLD_AREAS.keys():
            had_fresh = bool(original_area_digest.get(area_key))
            has_merged = bool(merged_area_digest.get(area_key))
            if not had_fresh and has_merged:
                stale_areas.append(area_key)
        return stale_areas

    def _flatten_headlines(self, area_digest: Dict[str, List[str]]) -> List[str]:
        flattened: List[str] = []
        for area_key in WORLD_AREAS.keys():
            for headline in area_digest.get(area_key, [])[:2]:
                if headline and headline not in flattened:
                    flattened.append(headline)
        return flattened

    def _build_world_state(self, locale: str = "", cached_data: Dict = None) -> Dict:
        now = datetime.now()
        raw_area_digest = self._fetch_area_news()
        area_digest = self._merge_with_cached_areas(raw_area_digest, cached_data or {})
        stale_areas = self._collect_stale_areas(raw_area_digest, area_digest)
        headlines = self._flatten_headlines(area_digest)
        weather_text = self._fetch_weather("Sao_Paulo" if not locale else locale)
        signals = self._build_signal_map(area_digest)
        atmosphere = self._derive_atmosphere(signals)
        dominant_tension = self._derive_dominant_tension(signals)
        dominant_category = signals[0]["category"] if signals else "indefinido"

        if signals:
            weighted = Counter()
            for signal in signals:
                weighted[signal["category"]] += float(signal.get("intensity", 0.0))
            dominant_category = weighted.most_common(1)[0][0]

        history = self._load_recent_history()
        continuity_note = self._derive_continuity_note(history, dominant_category)

        world_state = {
            "cache_timestamp": now.isoformat(),
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weather": weather_text,
            "headlines": headlines,
            "area_digest": area_digest,
            "stale_areas": stale_areas,
            "news": self._format_headlines(headlines),
            "signals": signals,
            "dominant_category": dominant_category,
            "dominant_tension": dominant_tension,
            "atmosphere": atmosphere,
            "continuity_note": continuity_note,
            "state_version": 3,
        }
        world_state["formatted_synthesis"] = self._format_synthesis(world_state)
        return world_state

    def _save_cache(self, world_state: Dict) -> None:
        try:
            with open(self.cache_file, "w", encoding="utf-8") as handle:
                json.dump(world_state, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("World Consciousness: erro ao salvar cache: %s", exc)

    def _load_cache(self) -> Dict:
        if not os.path.exists(self.cache_file):
            return {}

        try:
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            logger.warning("World Consciousness: falha ao ler cache: %s", exc)
            return {}

    def get_world_state(self, force_refresh: bool = False, locale: str = "") -> Dict:
        """
        Retrieves the current world state with lightweight persistence and
        multi-area interpretation for prompt injection.
        """
        now = datetime.now()
        cached_data = self._load_cache()

        if not force_refresh and cached_data:
            cached_time = datetime.fromisoformat(
                cached_data.get("cache_timestamp", "2000-01-01T00:00:00")
            )
            if now - cached_time < timedelta(hours=self.cache_duration_hours):
                logger.info("World Consciousness: carregando estado do mundo via cache.")
                cached_data["current_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                cached_data["formatted_synthesis"] = self._format_synthesis(cached_data)
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
    print("\nSINTESE GERADA:\n")
    print(state["formatted_synthesis"])
