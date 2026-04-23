from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from heapq import nlargest

from core.config import LLMSettings
from core.db import Repository
from core.llm import LLMProvider
from data.models import DEFAULT_CATEGORY_ID, MappingRecord, Process, SkillProcess

ProgressCallback = Callable[[str], None]
BatchCallback = Callable[[list[MappingRecord]], None]


class ProcessMapper:
    def __init__(self, repository: Repository, llm: LLMProvider, settings: LLMSettings) -> None:
        self.repository = repository
        self.llm = llm
        self.settings = settings

    def map_processes(
        self,
        processes: list[Process],
        employees: list[object],
        progress: ProgressCallback | None = None,
        category_id: str = DEFAULT_CATEGORY_ID,
        batch_size: int = 2,
        max_concurrency: int = 1,
        on_batch: BatchCallback | None = None,
    ) -> list[MappingRecord]:
        del employees
        skill_processes = self.repository.list_skill_processes(category_id, include_inactive=False)
        self._progress(progress, f"Loaded {len(skill_processes)} active standard processes.")

        records: list[MappingRecord] = []
        unresolved: list[Process] = []
        existing_hits = 0
        knowledge_hits = 0

        for process in processes:
            # Prefer exact style decisions first so human-reviewed corrections override generic knowledge.
            existing = self.repository.get_style_mapping(process.style_no, process.identity_hash, require_reason=True)
            if existing:
                records.append(
                    MappingRecord(
                        process_hash=process.identity_hash,
                        process_description=process.description,
                        llm_skill_name=existing.llm_skill_name,
                        final_skill_name=existing.final_skill_name,
                        confidence=existing.confidence,
                        human_approved=existing.human_approved,
                        reason=existing.reason or "existing style mapping",
                        suggested_new_skill=existing.suggested_new_skill,
                        mes_efficiency=existing.mes_efficiency,
                        llm_process_id=existing.llm_process_id,
                        final_process_id=existing.final_process_id,
                    )
                )
                existing_hits += 1
                continue
            knowledge = self.repository.find_mapping_knowledge(process.description)
            if (
                knowledge
                and knowledge.final_process_id
                and not knowledge.suggested_new_skill
                and (knowledge.human_approved or knowledge.confidence >= self.settings.confidence_threshold)
            ):
                # Cross-style knowledge is reused only when it already resolved to a concrete standard process.
                records.append(
                    MappingRecord(
                        process_hash=process.identity_hash,
                        process_description=process.description,
                        llm_skill_name=knowledge.llm_skill_name,
                        final_skill_name=knowledge.final_skill_name,
                        confidence=knowledge.confidence,
                        human_approved=knowledge.human_approved,
                        reason=knowledge.reason or "knowledge base hit",
                        suggested_new_skill=knowledge.suggested_new_skill,
                        mes_efficiency=knowledge.mes_efficiency,
                        llm_process_id=knowledge.llm_process_id,
                        final_process_id=knowledge.final_process_id,
                    )
                )
                knowledge_hits += 1
            else:
                unresolved.append(process)

        if existing_hits:
            self._progress(progress, f"Existing style mappings reused for {existing_hits} processes.")
        if knowledge_hits:
            self._progress(progress, f"Knowledge base reused for {knowledge_hits} processes.")
            if records and on_batch:
                on_batch(records.copy())
        if unresolved:
            records.extend(
                self._map_unresolved(
                    unresolved,
                    skill_processes,
                    progress,
                    batch_size=max(1, batch_size),
                    max_concurrency=max(1, max_concurrency),
                    on_batch=on_batch,
                )
            )
        suggested = sum(1 for record in records if not record.final_process_id or record.suggested_new_skill)
        self._progress(progress, f"Pending review mappings: {suggested}.")
        self._progress(progress, f"Mapping stage complete with {len(records)} rows.")
        return self._sort_records(processes, records)

    def _map_unresolved(
        self,
        processes: list[Process],
        skill_processes: list[SkillProcess],
        progress: ProgressCallback | None,
        *,
        batch_size: int,
        max_concurrency: int,
        on_batch: BatchCallback | None,
    ) -> list[MappingRecord]:
        if not getattr(self.llm, "enabled", False):
            self._progress(progress, "DeepSeek disabled; using local similarity fallback.")
            return [self._fallback_record(process, skill_processes, "DeepSeek disabled; used local similarity fallback") for process in processes]

        batches = [processes[index : index + batch_size] for index in range(0, len(processes), batch_size)]
        total_batches = len(batches)
        if max_concurrency <= 1 or total_batches <= 1:
            records: list[MappingRecord] = []
            for batch_index, batch in enumerate(batches, start=1):
                batch_records = self._map_single_batch(batch, skill_processes, progress, batch_index=batch_index, total_batches=total_batches)
                records.extend(batch_records)
                if on_batch:
                    on_batch(batch_records)
            return records

        records_by_index: dict[int, list[MappingRecord]] = {}
        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            # Preserve deterministic merge order even when batch requests complete out of order.
            futures = {
                executor.submit(self._map_single_batch, batch, skill_processes, progress, batch_index=index, total_batches=total_batches): index
                for index, batch in enumerate(batches, start=1)
            }
            for future in as_completed(futures):
                batch_index = futures[future]
                batch_records = future.result()
                records_by_index[batch_index] = batch_records
                if on_batch:
                    on_batch(batch_records)

        merged: list[MappingRecord] = []
        for batch_index in range(1, total_batches + 1):
            merged.extend(records_by_index.get(batch_index, []))
        return merged

    def _map_single_batch(
        self,
        batch: list[Process],
        skill_processes: list[SkillProcess],
        progress: ProgressCallback | None,
        *,
        batch_index: int,
        total_batches: int,
    ) -> list[MappingRecord]:
        candidate_pool = self._select_batch_candidates(batch, skill_processes)
        self._progress(
            progress,
            f"DeepSeek batch {batch_index}/{total_batches}: {len(batch)} processes, {len(candidate_pool)}/{len(skill_processes)} candidates.",
        )
        try:
            response = self.llm.map_process_batch(batch, candidate_pool)
            preview = response[0] if response else {}
            self._progress(progress, f"DeepSeek batch {batch_index}/{total_batches} returned {len(response)} rows; preview: {preview}")
            by_hash = {str(item.get('process_hash', '')): item for item in response}
            return [self._record_from_llm(process, skill_processes, by_hash.get(process.identity_hash)) for process in batch]
        except Exception as exc:
            self._progress(progress, f"DeepSeek batch {batch_index}/{total_batches} failed; local fallback: {exc}")
            return [self._fallback_record(process, skill_processes, "DeepSeek batch mapping failed; used local fallback") for process in batch]

    def _select_batch_candidates(
        self,
        batch: list[Process],
        skill_processes: list[SkillProcess],
        per_process_limit: int = 6,
        total_limit: int = 24,
    ) -> list[SkillProcess]:
        if len(skill_processes) <= total_limit:
            return skill_processes

        scored: dict[str, tuple[float, SkillProcess]] = {}
        for process in batch:
            # Candidate pruning keeps prompt size bounded while still giving each row local nearest neighbors.
            top_matches = nlargest(
                per_process_limit,
                ((self._candidate_score(process.description, skill_process), skill_process) for skill_process in skill_processes),
                key=lambda item: item[0],
            )
            for score, skill_process in top_matches:
                existing = scored.get(skill_process.id)
                if existing is None or score > existing[0]:
                    scored[skill_process.id] = (score, skill_process)

        ranked = sorted(scored.values(), key=lambda item: item[0], reverse=True)
        shortlisted = [skill_process for _, skill_process in ranked[:total_limit]]
        return shortlisted or skill_processes[:total_limit]

    def _record_from_llm(
        self,
        process: Process,
        skill_processes: list[SkillProcess],
        result: dict | None,
    ) -> MappingRecord:
        if not result:
            return self._fallback_record(process, skill_processes, "DeepSeek returned no result; used local fallback")

        process_id = str(result.get("process_id", "")).strip()
        display_name = str(result.get("display_name", "")).strip()
        reason = str(result.get("reason", "DeepSeek auto mapping"))
        confidence = _safe_confidence(result.get("confidence", 0.0))
        suggested_new_skill = bool(result.get("suggested_new_skill", False))

        matched = None
        if process_id:
            matched = next((item for item in skill_processes if item.id == process_id), None)
        if matched is None and display_name:
            matched = self.repository.match_skill_process_by_name(DEFAULT_CATEGORY_ID, display_name)

        final_process_id = matched.id if matched else ""
        final_name = matched.display_name if matched else display_name
        # Auto-approval is allowed only for confident matches that resolve to an existing standard process.
        human_approved = bool(final_process_id) and confidence >= self.settings.confidence_threshold and not suggested_new_skill
        return MappingRecord(
            process_hash=process.identity_hash,
            process_description=process.description,
            llm_skill_name=display_name,
            final_skill_name=final_name,
            confidence=confidence,
            human_approved=human_approved,
            reason=reason,
            suggested_new_skill=suggested_new_skill or not bool(final_process_id),
            llm_process_id=process_id,
            final_process_id=final_process_id,
        )

    def _fallback_record(self, process: Process, skill_processes: list[SkillProcess], reason: str) -> MappingRecord:
        matched, confidence = self._fallback_match(process.description, skill_processes)
        return MappingRecord(
            process_hash=process.identity_hash,
            process_description=process.description,
            llm_skill_name=matched.display_name if matched else "Pending confirmation",
            final_skill_name=matched.display_name if matched else "",
            confidence=confidence,
            human_approved=False,
            reason=reason,
            # Fallback never silently approves; users must review these rows explicitly.
            suggested_new_skill=matched is None,
            llm_process_id=matched.id if matched else "",
            final_process_id=matched.id if matched and confidence >= self.settings.confidence_threshold else "",
        )

    @staticmethod
    def _fallback_match(description: str, skill_processes: list[SkillProcess]) -> tuple[SkillProcess | None, float]:
        if not skill_processes:
            return None, 0.0
        scores = []
        for process in skill_processes:
            score = ProcessMapper._candidate_score(description, process)
            scores.append((process, score))
        matched, score = max(scores, key=lambda item: item[1])
        return matched, min(score, 0.79)

    @staticmethod
    def _candidate_score(description: str, process: SkillProcess) -> float:
        candidates = [process.display_name, *process.aliases]
        return max(SequenceMatcher(None, description, candidate).ratio() for candidate in candidates if candidate)

    @staticmethod
    def _sort_records(processes: list[Process], records: list[MappingRecord]) -> list[MappingRecord]:
        order = {process.identity_hash: index for index, process in enumerate(processes)}
        return sorted(records, key=lambda record: order.get(record.process_hash, 10**9))

    @staticmethod
    def _progress(progress: ProgressCallback | None, message: str) -> None:
        if progress:
            progress(message)


def is_human_confirmed(records: list[MappingRecord], threshold: float) -> bool:
    if not records:
        return False
    return all(
        record.final_process_id
        and record.final_skill_name
        and (record.confidence >= threshold or record.human_approved)
        and (not record.suggested_new_skill or record.human_approved)
        for record in records
    )


def _safe_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
