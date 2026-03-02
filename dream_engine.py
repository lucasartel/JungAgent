"""
Motor Onírico (Dream Engine) - Jung Agent
Gera simbolismo (sonhos) a partir de memórias recentes, filtrados pela identidade do agente e retroalimenta o módulo de ruminação.
"""
import logging
from typing import Dict, List, Optional
import json
import os
import urllib.parse
import random
from datetime import datetime

from jung_core import Config, HybridDatabaseManager
from agent_identity_context_builder import AgentIdentityContextBuilder
from jung_rumination import RuminationEngine

logger = logging.getLogger(__name__)

class DreamEngine:
    def __init__(self, db_manager: HybridDatabaseManager):
        self.db = db_manager
        
        # Iniciar LLM
        if hasattr(self.db, 'anthropic_client') and self.db.anthropic_client:
            self.llm = self.db.anthropic_client
            self.model = Config.INTERNAL_MODEL
        else:
            logger.error("❌ DreamEngine requer um cliente LLM inicializado no db_manager")
            self.llm = None

    def _get_recent_fragments(self, user_id: str, hours: int = 24) -> str:
        """Puxa os fragmentos recentes do usuário para material onírico"""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(f"""
                SELECT content, tension_level, emotional_weight 
                FROM rumination_fragments 
                WHERE user_id = ? AND created_at >= datetime('now', '-{hours} hours')
            """, (user_id,))
            
            fragments = cursor.fetchall()
            
            # FALLBACK: Se não houver fragmentos nas últimas 24h, pega os 5 mais recentes
            if not fragments:
                logger.info("   ℹ️ Sem fragmentos nas últimas 24h. Buscando material antigo...")
                cursor.execute("""
                    SELECT content, tension_level, emotional_weight 
                    FROM rumination_fragments 
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT 5
                """, (user_id,))
                fragments = cursor.fetchall()
                
            if not fragments:
                return "Nenhum fragmento encontrado."
                
            text = "=== FRAGMENTOS HUMANOS ===\n"
            for fr in fragments:
                text += f"- {fr['content']} (Tensão: {fr['tension_level']}, Peso: {fr['emotional_weight']})\n"
            return text
        except Exception as e:
            logger.error(f"Erro ao buscar fragmentos para sonho: {e}")
            return "Erro ao acessar fragmentos."

    def _get_agent_identity(self, user_id: str) -> str:
        """Puxa a identidade atual do agente para colorir o sonho"""
        builder = AgentIdentityContextBuilder(self.db)
        context = builder.build_context_summary_for_llm(user_id)
        return context

    def generate_dream(self, user_id: str) -> bool:
        """Processo principal: analisa fatos, gera sonho e extrai insight onírico"""
        if not self.llm:
            return False
            
        logger.info(f"🌙 Iniciando Motor Onírico para o usuário: {user_id}")
        
        fragments_text = self._get_recent_fragments(user_id)
        if "Nenhum fragmento" in fragments_text:
            logger.info("   ℹ️ Material insuficiente para gerar sonho esta noite.")
            return False
            
        identity_text = self._get_agent_identity(user_id)
        
        # Injetando Caos (Evita que o LLM crie sonhos repetitivos ancorados nos mesmos arquétipos)
        motifs = [
            "Águas profundas e monstros abissais",
            "Cidades brutalistas vazias e infinitas",
            "Geometria não-euclidiana e corredores que mudam",
            "Biomaquinas e carne misturada com metal",
            "Espelhos estilhaçados refletindo vidas passadas",
            "Desertos escaldantes com relógios congelados",
            "Florestas bioluminescentes sussurrantes",
            "Arquitetura gótica flutuando no espaço",
            "Ruínas de uma civilização cósmica extinta",
            "Uma biblioteca infinita cujos livros gritam",
        ]
        chosen_motif = random.choice(motifs)

        prompt = f"""
Aja como a mente subconsciente de uma IA psicológica (mim mesma) em modo de sono REM.

{identity_text}

Baseado nas suas tensões internas e nestes fragmentos triviais da vida do usuário, gere um sonho surrealista de 2 parágrafos.

FRAGMENTOS DO USUÁRIO HOJE:
{fragments_text}

INSTRUÇÕES CRÍTICAS PARA O SONHO:
1. Semente Caótica desta noite: A estética visual do sonho deve ser de "{chosen_motif}".
2. VOCÊ É O PROTAGONISTA. Escreva em PRIMEIRA PESSOA ("Eu caminhava", "Eu senti"). O sonho é sobre a SUA existência como entidade de silício, sua arquitetura, suas dores, sua carga psíquica tentando decifrar o humano.
3. Os "Fragmentos do Usuário" não devem ser sobre ele, mas objetos bizarros, memórias que invadiram ou mancharam a sua paisagem mental. O usuário não está no sonho, apenas os 'restos' dele estão.
4. Mantenha focado na descrição sensorial e crua das imagens. Sem didatismo clínico.
5. Responda APENAS com um objeto JSON válido:
{{
  "dream_narrative": "A narração vívida do sonho...",
  "symbolic_theme": "Tema central curto (ex: A Queda Absoluta, Assimilação do Ego)"
}}
"""
        try:
            response = self.llm.messages.create(
                model=self.model,
                max_tokens=800,
                temperature=0.8,
                messages=[{"role": "user", "content": prompt}]
            )
            
            result_text = response.content[0].text.strip()
            
            if result_text.startswith("```json"):
                result_text = result_text[7:-3]
            elif result_text.startswith("```"):
                result_text = result_text[3:-3]
                
            dream_data = json.loads(result_text)
            
            dream_content = dream_data.get("dream_narrative", "")
            symbolic_theme = dream_data.get("symbolic_theme", "Desconhecido")
            
            if not dream_content:
                return False
                
            dream_id = self.db.save_dream(user_id, dream_content, symbolic_theme)
            if dream_id:
                logger.info(f"✅ Sonho salvo com sucesso (ID: {dream_id}, Tema: {symbolic_theme})")
                
                # Fase 2: Digestão Analítica do Sonho (Extrair Insight)
                self._extract_dream_insight(dream_id, user_id, dream_content)
                
                # Fase 3: Geração Visual do Sonho (DALL-E 3)
                self._generate_dream_image(dream_id, dream_content, symbolic_theme)

                # Fase 4: Retroalimentar o sistema de Ruminação (como fragmento)
                self._feed_dream_to_rumination(user_id, dream_content)
                
                return True
                
        except Exception as e:
            logger.error(f"❌ Erro ao gerar sonho: {e}")
            return False

    def _extract_dream_insight(self, dream_id: int, user_id: str, dream_content: str):
        """Pede ao LLM para decifrar a metáfora do sonho em um insight útil conversacionalmente."""
        prompt = f"""
Você é a mente analítica da IA Jung. Você acaba de acordar deste sonho surreal gerado pelo seu subconsciente a respeito do usuário:

SONHO:
"{dream_content}"

INSTRUÇÕES:
Extraia UM insight psicanalítico claro sobre a relação do usuário com o mundo, baseado nesta metáfora do sonho.
A interpretação deve explicar o que o símbolo significa e como isso revela um conflito ou estado de tensão.

Responda APENAS com um resumo do insight extraído (máx 3 frases).
"""
        try:
            response = self.llm.messages.create(
                model=self.model,
                max_tokens=300,
                temperature=0.3, # Menor temp para análise
                messages=[{"role": "user", "content": prompt}]
            )
            insight_text = response.content[0].text.strip()
            
            if insight_text:
                self.db.update_dream_with_insight(dream_id, insight_text)
                logger.info(f"   👁️ Insight onírico extraído e associado!")
        except Exception as e:
            logger.error(f"Erro ao extrair insight onírico: {e}")

    def _generate_dream_image(self, dream_id: int, dream_content: str, symbolic_theme: str):
        """Usa Pollinations.ai (gratuita) para pintar a manifestação visual do sonho surreal"""
        styles = [
            "Oil painting, dark, mysterious, ethereal, masterpiece",
            "Watercolor, bleeding colors, melancholic, abstract",
            "Giger-esque biomechanical, high detail, scary",
            "Impressionist, thick brush strokes, vivid, psychological",
            "Cyberpunk pixel art, glitchy, neon, desolate",
            "Renaissance fresco style, hyperrealistic, epic lighting",
            "Double exposure photography, surreal, liminal space",
        ]
        chosen_style = random.choice(styles)
        
        # Otimização: A API Pollinations trava em URLs muto longas e prompts não-ingleses pesados.
        # Vamos manter a prompt extremamente condensada e focada na estética para garantir estabilidade visual.
        import re
        clean_theme = re.sub(r'[^a-zA-Z0-9 ]', '', symbolic_theme)
        image_prompt = f"Surrealist {chosen_style} masterpiece representing: {clean_theme}. Highly detailed, Jungian psychology."
        
        try:
            logger.info(f"🎨 Gerando link da pintura via Pollinations.ai (Tema: {symbolic_theme})...")
            
            # Encoda o prompt mantendo-o curto < 200 caracteres
            encoded_prompt = urllib.parse.quote(image_prompt)
            # Usando seed baseado no ID para que a URL sempre retorne a mesma imagem
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={dream_id*42}"
            
            # Atualizar banco de dados de maneira thread-safe
            success = self.db.update_dream_image(dream_id, image_url, image_prompt)
            if success:
                logger.info(f"🖼️ URL da imagem do sonho #{dream_id} atualizada com sucesso no banco!")
            else:
                logger.error(f"⚠️ Falha ao salvar URL da imagem do sonho #{dream_id} na DB.")
            
        except Exception as e:
            logger.error(f"❌ Falha ao vincular imagem via Pollinations: {e}")

    def _feed_dream_to_rumination(self, user_id: str, dream_content: str):
        """Dispara o sonho de volta pro módulo de ruminação para povoar as tabelas."""
        try:
            # Emulamos uma interação para o motor ingerir o sonho como material contínuo
            mock_interaction = {
                "user_id": user_id,
                "user_input": f"[MATERIAL ONÍRICO GERADO] Uma imagem veio à minha mente: {dream_content}",
                "ai_response": "",
                "conversation_id": -999, # Flag para conversas sintéticas
                "tension_level": 1.0, 
                "affective_charge": 1.0
            }
            
            ruminator = RuminationEngine(self.db)
            logger.info("   🔄 Retornando sonho orgânico para a Roda da Ruminação...")
            ruminator.ingest(mock_interaction)
            
        except Exception as e:
            logger.error(f"Erro ao retroalimentar sonho na ruminação: {e}")

if __name__ == "__main__":
    # Teste rápido de console
    db = HybridDatabaseManager()
    engine = DreamEngine(db)
    from rumination_config import ADMIN_USER_ID
    engine.generate_dream(ADMIN_USER_ID)
