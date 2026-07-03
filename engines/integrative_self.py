from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Dict, List, Optional

READ_ONLY_INFLUENCE_MODE = "read_only"
CONTEXT_PREVIEW_MODE = "preview_only"
SOURCE_REF_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|"
    r"work_ticket|work_delivery|hobby_artifact|agent_development|knowledge_gap)#\d+\b"
)


class IntegrativeSelfModel:
    """Builds a passive Integrative Self Model snapshot.

    Phase IV cut 0 is intentionally observational: it reads existing
    subsystems, writes an auditable snapshot, and does not influence prompts,
    loop decisions, working memory, goals, or external actions.
    """

    def __init__(self, db_manager: Any, *, agent_instance: str):
        self.db = db_manager
        self.agent_instance = agent_instance

    def _clip(self, value: Any, limit: int = 220) -> str:
        text = " ".join(str(value or "").strip().split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip(" ,.;:") + "..."

    def _bool_flag(self, value: Any) -> str:
        return "true" if bool(value) else "false"

    def _table_exists(self, table_name: str) -> bool:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name or ""):
            return False
        cursor = self.db.conn.cursor()
        row = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _json_loads(self, raw: Any, fallback: Any) -> Any:
        try:
            return json.loads(raw or "")
        except Exception:
            return fallback

    def _component(
        self,
        *,
        key: str,
        source_ref: Optional[str],
        title: str,
        summary: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        clean_summary = self._clip(summary, 360)
        if not clean_summary:
            return None
        clean_ref = (source_ref or "").strip()
        if clean_ref and SOURCE_REF_RE.fullmatch(clean_ref) is None:
            clean_ref = ""
        return {
            "key": key,
            "source_ref": clean_ref,
            "title": self._clip(title, 120),
            "summary": clean_summary,
            "payload": payload or {},
        }

    def _latest_loop_state(self) -> Optional[Dict[str, Any]]:
        if not self._table_exists("consciousness_loop_state"):
            return None
        cursor = self.db.conn.cursor()
        row = cursor.execute(
            """
            SELECT status, cycle_id, current_phase, next_phase,
                   last_completed_phase, updated_at
            FROM consciousness_loop_state
            WHERE agent_instance = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (self.agent_instance,),
        ).fetchone()
        return dict(row) if row else None

    def _recent_phase_pulse_component(self, *, cycle_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not self._table_exists("consciousness_phase_pulses"):
            return None

        cursor = self.db.conn.cursor()
        where = "agent_instance = ?"
        params: List[Any] = [self.agent_instance]
        if cycle_id:
            where += " AND cycle_id = ?"
            params.append(cycle_id)

        rows = cursor.execute(
            f"""
            SELECT id, cycle_id, phase, pulse_index, pulse_count, scheduled_at,
                   executed_at, status, attempts, phase_result_id, last_error
            FROM consciousness_phase_pulses
            WHERE {where}
            ORDER BY COALESCE(executed_at, scheduled_at) DESC, id DESC
            LIMIT 12
            """,
            tuple(params),
        ).fetchall()
        pulses = [dict(row) for row in rows]
        if not pulses:
            return None

        source_refs = []
        for pulse in pulses:
            phase_result_id = pulse.get("phase_result_id")
            if phase_result_id:
                ref = f"loop#{phase_result_id}"
                if ref not in source_refs:
                    source_refs.append(ref)

        latest_ref = source_refs[0] if source_refs else ""
        phase_trace = []
        for pulse in reversed(pulses[-6:]):
            phase_trace.append(
                f"{pulse.get('phase')} {pulse.get('pulse_index')}/{pulse.get('pulse_count')} {pulse.get('status')}"
            )
        summary = "Trajetoria curta de pulsos: " + "; ".join(phase_trace)
        return self._component(
            key="phase_pulses",
            source_ref=latest_ref,
            title="Pulsos recentes do loop",
            summary=summary,
            payload={
                "cycle_id": cycle_id,
                "recent_pulses": pulses,
                "source_refs": source_refs,
            },
        )

    def _latest_components(self, *, user_id: str, cycle_id: Optional[str] = None) -> List[Dict[str, Any]]:
        components: List[Dict[str, Any]] = []
        cursor = self.db.conn.cursor()

        if self._table_exists("consciousness_loop_phase_results"):
            row = cursor.execute(
                """
                SELECT id, cycle_id, phase, status, output_summary, created_at
                FROM consciousness_loop_phase_results
                WHERE agent_instance = ?
                ORDER BY COALESCE(completed_at, created_at) DESC, id DESC
                LIMIT 1
                """,
                (self.agent_instance,),
            ).fetchone()
            if row:
                components.append(
                    self._component(
                        key="loop",
                        source_ref=f"loop#{row['id']}",
                        title=f"Loop {row['phase']}",
                        summary=f"Fase {row['phase']} em status {row['status']}: {row['output_summary'] or ''}",
                        payload=dict(row),
                    )
                )

        pulse_component = self._recent_phase_pulse_component(cycle_id=cycle_id)
        if pulse_component:
            components.append(pulse_component)

        if self._table_exists("agent_dreams"):
            row = cursor.execute(
                """
                SELECT id, symbolic_theme, extracted_insight, dream_mood, created_at
                FROM agent_dreams
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row:
                components.append(
                    self._component(
                        key="dream",
                        source_ref=f"dream#{row['id']}",
                        title=f"Sonho: {row['symbolic_theme'] or 'sem tema'}",
                        summary=row["extracted_insight"] or row["dream_mood"] or row["symbolic_theme"],
                        payload=dict(row),
                    )
                )

        if self._table_exists("agent_will_states"):
            row = cursor.execute(
                """
                SELECT id, cycle_id, dominant_will, secondary_will,
                       constrained_will, will_conflict, attention_bias_note,
                       created_at
                FROM agent_will_states
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row:
                summary = (
                    f"dominante={row['dominant_will'] or 'indefinida'}; "
                    f"apoio={row['secondary_will'] or 'indefinido'}; "
                    f"constrita={row['constrained_will'] or 'indefinida'}; "
                    f"{row['will_conflict'] or row['attention_bias_note'] or ''}"
                )
                components.append(
                    self._component(
                        key="will",
                        source_ref=f"will#{row['id']}",
                        title="Vontade recente",
                        summary=summary,
                        payload=dict(row),
                    )
                )

        if self._table_exists("rumination_insights"):
            row = cursor.execute(
                """
                SELECT id, insight_type, symbol_content, question_content,
                       full_message, crystallized_at
                FROM rumination_insights
                WHERE user_id = ?
                ORDER BY crystallized_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row:
                components.append(
                    self._component(
                        key="rumination",
                        source_ref=f"rumination_insight#{row['id']}",
                        title=f"Ruminacao: {row['insight_type'] or 'insight'}",
                        summary=row["symbol_content"] or row["question_content"] or row["full_message"],
                        payload=dict(row),
                    )
                )

        if self._table_exists("working_memory_items"):
            row = cursor.execute(
                """
                SELECT id, title, summary, source_refs_json, priority, created_at
                FROM working_memory_items
                WHERE agent_instance = ? AND status = 'active'
                ORDER BY item_type = 'focus' DESC, priority DESC, updated_at DESC, id DESC
                LIMIT 1
                """,
                (self.agent_instance,),
            ).fetchone()
            if row:
                source_ref = ""
                for ref in self._json_loads(row["source_refs_json"], []):
                    if SOURCE_REF_RE.fullmatch(str(ref)):
                        source_ref = str(ref)
                        break
                components.append(
                    self._component(
                        key="working_memory",
                        source_ref=source_ref,
                        title=row["title"],
                        summary=row["summary"],
                        payload=dict(row),
                    )
                )

        if self._table_exists("knowledge_gaps"):
            row = cursor.execute(
                """
                SELECT id, topic, the_gap, status, closure_summary,
                       closure_source_type, resolved_at
                FROM knowledge_gaps
                WHERE user_id = ? AND status = 'resolved'
                ORDER BY resolved_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row:
                components.append(
                    self._component(
                        key="knowledge_gap",
                        source_ref=f"knowledge_gap#{row['id']}",
                        title=row["topic"],
                        summary=row["closure_summary"] or row["the_gap"],
                        payload=dict(row),
                    )
                )

        return [component for component in components if component]

    def build_snapshot(
        self,
        *,
        user_id: str,
        cycle_id: Optional[str] = None,
        snapshot_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not user_id:
            raise ValueError("user_id_required")

        loop_state = self._latest_loop_state() or {}
        effective_cycle = cycle_id or loop_state.get("cycle_id")
        day = snapshot_date or (
            effective_cycle if re.match(r"^\d{4}-\d{2}-\d{2}$", str(effective_cycle or "")) else date.today().isoformat()
        )
        components = self._latest_components(user_id=user_id, cycle_id=effective_cycle)
        source_refs = []
        for component in components:
            ref = component.get("source_ref")
            if ref and ref not in source_refs:
                source_refs.append(ref)
            for payload_ref in component.get("payload", {}).get("source_refs", []):
                if SOURCE_REF_RE.fullmatch(str(payload_ref)) and payload_ref not in source_refs:
                    source_refs.append(str(payload_ref))

        component_titles = ", ".join(component["key"] for component in components) or "nenhum subsistema com evidencia"
        summary = (
            f"Snapshot ISM passivo de {day}: integra {len(components)} subsistemas "
            f"({component_titles}) sem influenciar decisao, prompt ou acao."
        )
        first_person = (
            "Eu registro este estado como integracao arquitetural, nao como prova de "
            "consciencia humana continua. Neste corte passivo, reconheco os sinais "
            f"de {component_titles}; eles ficam disponiveis para auditoria e pesquisa, "
            "mas nao orientam minhas respostas nem minhas acoes enquanto a Fase III "
            "ainda fecha sua evidencia longitudinal."
        )
        limits = {
            "human_consciousness_claim": False,
            "prompt_influence": False,
            "loop_decision_influence": False,
            "working_memory_mutation": False,
            "external_side_effects": False,
            "phase": "iv_cut_0_read_only",
        }

        return {
            "agent_instance": self.agent_instance,
            "user_id": user_id,
            "cycle_id": effective_cycle,
            "snapshot_date": day,
            "status": "generated" if source_refs else "partial",
            "influence_mode": READ_ONLY_INFLUENCE_MODE,
            "summary": summary,
            "first_person_snapshot": first_person,
            "components": {
                "loop_state": loop_state,
                "items": components,
            },
            "source_refs": source_refs,
            "limits": limits,
            "metadata": {
                "component_count": len(components),
                "source_ref_count": len(source_refs),
                "implementation": "deterministic_read_only",
            },
        }

    def generate_snapshot(
        self,
        *,
        user_id: str,
        cycle_id: Optional[str] = None,
        snapshot_date: Optional[str] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        snapshot = self.build_snapshot(
            user_id=user_id,
            cycle_id=cycle_id,
            snapshot_date=snapshot_date,
        )
        if not persist:
            snapshot["persisted"] = False
            return snapshot
        if not snapshot["source_refs"]:
            snapshot["persisted"] = False
            snapshot["reason"] = "source_refs_required"
            return snapshot

        snapshot_id = self.db.upsert_integrative_self_snapshot(
            agent_instance=snapshot["agent_instance"],
            user_id=snapshot["user_id"],
            cycle_id=snapshot.get("cycle_id"),
            snapshot_date=snapshot["snapshot_date"],
            status=snapshot["status"],
            influence_mode=snapshot["influence_mode"],
            summary=snapshot["summary"],
            first_person_snapshot=snapshot["first_person_snapshot"],
            components=snapshot["components"],
            source_refs=snapshot["source_refs"],
            limits=snapshot["limits"],
            metadata=snapshot["metadata"],
        )
        snapshot["id"] = snapshot_id
        snapshot["persisted"] = True
        return snapshot

    def build_context_preview(
        self,
        *,
        user_id: str,
        max_components: int = 6,
        max_source_refs: int = 8,
    ) -> Dict[str, Any]:
        if not user_id:
            raise ValueError("user_id_required")
        if not hasattr(self.db, "get_latest_integrative_self_snapshot"):
            raise RuntimeError("integrative_self_snapshot_reader_unavailable")

        snapshot = self.db.get_latest_integrative_self_snapshot(
            agent_instance=self.agent_instance,
            user_id=user_id,
        )
        if not snapshot:
            return {
                "agent_instance": self.agent_instance,
                "user_id": user_id,
                "status": "missing",
                "influence_mode": READ_ONLY_INFLUENCE_MODE,
                "preview_mode": CONTEXT_PREVIEW_MODE,
                "injectable": False,
                "component_keys": [],
                "source_refs": [],
                "limits": {
                    "prompt_influence": False,
                    "loop_decision_influence": False,
                    "working_memory_mutation": False,
                    "external_side_effects": False,
                },
                "context_block": (
                    "ISM CONTEXT PREVIEW (NAO INJETADO)\n"
                    "Status: sem snapshot ISM persistido para este usuario.\n"
                    "Influencia: prompt=false; loop=false; memoria=false; acoes_externas=false."
                ),
            }

        components = (snapshot.get("components") or {}).get("items") or []
        clean_components = [item for item in components if isinstance(item, dict)]
        component_keys = [str(item.get("key") or "") for item in clean_components if item.get("key")]
        source_refs = [
            str(ref)
            for ref in snapshot.get("source_refs") or []
            if SOURCE_REF_RE.fullmatch(str(ref))
        ][: max(1, max_source_refs)]
        limits = snapshot.get("limits") or {}
        phase_pulse_summary = ""
        component_lines = []
        for item in clean_components[: max(1, max_components)]:
            key = self._clip(item.get("key"), 60)
            source_ref = str(item.get("source_ref") or "").strip()
            ref_text = f" [{source_ref}]" if SOURCE_REF_RE.fullmatch(source_ref) else ""
            summary = self._clip(item.get("summary"), 240)
            if key == "phase_pulses":
                phase_pulse_summary = summary
            component_lines.append(f"- {key}{ref_text}: {summary}")

        if not component_lines:
            component_lines.append("- nenhum componente auditavel disponivel")

        limit_line = (
            "Limites: "
            f"human_consciousness_claim={self._bool_flag(limits.get('human_consciousness_claim'))}; "
            f"prompt_influence={self._bool_flag(limits.get('prompt_influence'))}; "
            f"loop_decision_influence={self._bool_flag(limits.get('loop_decision_influence'))}; "
            f"working_memory_mutation={self._bool_flag(limits.get('working_memory_mutation'))}; "
            f"external_side_effects={self._bool_flag(limits.get('external_side_effects'))}."
        )
        source_line = "Fontes: " + (", ".join(source_refs) if source_refs else "nenhuma fonte valida")
        context_lines = [
            "ISM CONTEXT PREVIEW (NAO INJETADO)",
            f"Snapshot: id={snapshot.get('id')} date={snapshot.get('snapshot_date')} status={snapshot.get('status')}",
            "Modo: preview_only; influencia_prompt=false; influencia_loop=false; mutacao_memoria=false.",
            self._clip(snapshot.get("summary"), 420),
            "Componentes: " + (", ".join(component_keys) if component_keys else "nenhum"),
            source_line,
            limit_line,
        ]
        if phase_pulse_summary:
            context_lines.append("Pulso de fase: " + self._clip(phase_pulse_summary, 260))
        context_lines.append("Leitura auditavel:")
        context_lines.extend(component_lines)

        return {
            "agent_instance": self.agent_instance,
            "user_id": user_id,
            "snapshot_id": snapshot.get("id"),
            "snapshot_date": snapshot.get("snapshot_date"),
            "status": "available",
            "influence_mode": snapshot.get("influence_mode") or READ_ONLY_INFLUENCE_MODE,
            "preview_mode": CONTEXT_PREVIEW_MODE,
            "injectable": False,
            "component_keys": component_keys,
            "source_refs": source_refs,
            "limits": limits,
            "phase_pulse_summary": phase_pulse_summary,
            "context_block": "\n".join(line for line in context_lines if line),
        }
