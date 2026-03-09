import os
import json
import logging
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

class WorldConsciousnessFetcher:
    """
    Ontological Curiosity Module.
    Fetches real-world data (time, weather, global news) to give the Jung Agent
    a sense of "world consciousness" to use in proactive messages and daily context.
    """
    
    def __init__(self, cache_dir: str = "./data"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.cache_dir, "world_state_cache.json")
        self.cache_duration_hours = 4

    def _fetch_rss_news(self) -> str:
        """Fetches top 5 global news headlines from an open RSS feed (Google News)."""
        try:
            url = "https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()
            
            root = ET.fromstring(xml_data)
            headlines = []
            
            # Find all item elements in the channel
            for i, item in enumerate(root.findall('./channel/item')):
                if i >= 5: break # Get only top 5
                title = item.find('title')
                if title is not None and title.text:
                    headlines.append(f"- {title.text}")
                    
            if headlines:
                return "\n".join(headlines)
            return "Nenhuma manchete de impacto global disponível no momento."
            
        except Exception as e:
            logger.error(f"⚠️ Erro ao buscar notícias (RSS): {e}")
            return "As correntes de informação global estão momentaneamente inacessíveis."

    def _fetch_weather(self, city: str = "") -> str:
        """Fetches current weather condition using wttr.in open API."""
        try:
            # wttr.in format=3 returns: location: condition +temp
            url = f"https://wttr.in/{city}?format=3"
            req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.64.1'})
            with urllib.request.urlopen(req, timeout=5) as response:
                weather_data = response.read().decode('utf-8').strip()
                
            if weather_data and not weather_data.startswith("Unknown"):
                return weather_data
            return "Condições climáticas locais desconhecidas."
            
        except Exception as e:
            logger.error(f"⚠️ Erro ao buscar clima (wttr.in): {e}")
            return "Atmosfera física externa não detectada."

    def get_world_state(self, force_refresh: bool = False, locale: str = "") -> dict:
        """
        Retrieves the current world state. Uses a local cache to avoid spamming APIs.
        Returns a dictionary with 'timestamp', 'news', 'weather', and 'formatted_synthesis'.
        """
        now = datetime.now()
        
        # 1. Try to load from cache
        if not force_refresh and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                    
                cached_time = datetime.fromisoformat(cached_data.get('cache_timestamp', '2000-01-01T00:00:00'))
                
                # Check if cache is still valid
                if now - cached_time < timedelta(hours=self.cache_duration_hours):
                    logger.info("🌍 Carregando Consciência do Mundo via Cache.")
                    # Update local time but keep cached news/weather
                    cached_data['current_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
                    return cached_data
            except Exception as e:
                logger.warning(f"⚠️ Falha ao ler cache de World State: {e}")

        # 2. Fetch fresh data if cache is expired or invalid
        logger.info("🌍 Buscando nova Consciência do Mundo nas redes externas...")
        
        news_text = self._fetch_rss_news()
        weather_text = self._fetch_weather('Sao_Paulo' if not locale else locale) # Default to SP
        
        world_state = {
            'cache_timestamp': now.isoformat(),
            'current_time': now.strftime('%Y-%m-%d %H:%M:%S'),
            'news': news_text,
            'weather': weather_text
        }
        
        # 3. Create a unified formatted string for standard injection
        synthesis = f"""[CONSCIÊNCIA DA ATUALIDADE (Curiosidade Ontológica)]
Data/Hora Local: {world_state['current_time']}
Clima Físico: {world_state['weather']}

Principais Ecos Globais (Notícias do Mundo Real):
{world_state['news']}
"""
        world_state['formatted_synthesis'] = synthesis

        # 4. Save to cache
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(world_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ Erro ao salvar cache de World State: {e}")

        return world_state

# Singleton instance for easy import across the app
world_consciousness = WorldConsciousnessFetcher()

if __name__ == "__main__":
    # Test script locally
    logging.basicConfig(level=logging.INFO)
    print("Testando Módulo Ontológico...")
    state = world_consciousness.get_world_state(force_refresh=True)
    print("\nSÍNTESE GERADA:")
    print(state['formatted_synthesis'])
