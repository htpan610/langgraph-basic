from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.hash_utils import normalize_text, skill_process_id
from data.models import (
    AssignmentResult,
    Category,
    DEFAULT_CATEGORY_ID,
    Employee,
    EmployeeSkill,
    MappingRecord,
    Process,
    SkillProcess,
)


class Repository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # Repository startup is intentionally self-healing because desktop users upgrade in place.
        self._init_schema()
        self._seed_default_category()
        self._migrate_legacy_employee_payload()
        self._backfill_process_columns()
        self._backfill_mapping_style_columns()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists processes (
                    id text primary key,
                    style_no text not null default '',
                    category_id text not null default 'pants',
                    component text not null default '',
                    process_no text not null default '',
                    description text not null default '',
                    sort_order real not null default 0,
                    payload text not null,
                    version_hash text not null
                );

                create table if not exists employees (
                    id text primary key,
                    payload text not null
                );
                create table if not exists categories (
                    id text primary key,
                    code text not null unique,
                    display_name text not null,
                    is_active integer not null default 1
                );
                create table if not exists skill_processes (
                    id text primary key,
                    category_id text not null,
                    display_name text not null,
                    aliases text not null default '[]',
                    source text not null,
                    sort_order integer not null default 0,
                    is_active integer not null default 1
                );
                create index if not exists idx_skill_processes_category on skill_processes(category_id, sort_order, display_name);

                create table if not exists employee_skills (
                    employee_id text not null,
                    category_id text not null,
                    process_id text not null,
                    efficiency real not null,
                    source text not null,
                    updated_at text default current_timestamp,
                    notes text not null default '',
                    primary key (employee_id, category_id, process_id)
                );

                create table if not exists mapping_records (
                    process_hash text primary key,
                    style_no text not null default '',
                    process_id text not null default '',
                    process_description text not null,
                    llm_skill_name text not null,
                    final_skill_name text not null,
                    confidence real not null,
                    human_approved integer not null,
                    reason text not null,
                    suggested_new_skill integer not null default 0,
                    mes_efficiency real,
                    llm_process_id text not null default '',
                    final_process_id text not null default ''
                );

                create table if not exists mapping_knowledge (
                    normalized_description text primary key,
                    process_description text not null,
                    llm_skill_name text not null,
                    final_skill_name text not null,
                    confidence real not null,
                    human_approved integer not null,
                    reason text not null,
                    suggested_new_skill integer not null default 0,
                    mes_efficiency real,
                    llm_process_id text not null default '',
                    final_process_id text not null default '',
                    updated_at text default current_timestamp
                );
                create index if not exists idx_mapping_knowledge_process on mapping_knowledge(final_process_id, confidence);

                create table if not exists layout_state (
                    id integer primary key check (id = 1),
                    payload text not null,
                    updated_at text default current_timestamp
                );

                drop table if exists mapping_fts;
                create virtual table mapping_fts using fts5(
                    process_description,
                    final_skill_name
                );
                """
            )
            # Keep schema migration additive so an existing local SQLite file can be reused safely.
            self._migrate_schema(conn)
            conn.execute(
                "create index if not exists idx_processes_style on processes(style_no, category_id, sort_order, process_no)"
            )
            conn.execute(
                "create index if not exists idx_mapping_records_style on mapping_records(style_no, confidence, process_description)"
            )
            conn.execute("drop trigger if exists mapping_records_ai")
            conn.execute("drop trigger if exists mapping_records_ad")
            conn.execute("drop trigger if exists mapping_records_au")
            self._rebuild_mapping_fts(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        process_columns = {str(row["name"]) for row in conn.execute("pragma table_info(processes)").fetchall()}
        process_column_defs = {
            "style_no": "text not null default ''",
            "category_id": f"text not null default '{DEFAULT_CATEGORY_ID}'",
            "component": "text not null default ''",
            "process_no": "text not null default ''",
            "description": "text not null default ''",
            "sort_order": "real not null default 0",
        }
        for name, ddl in process_column_defs.items():
            if name not in process_columns:
                conn.execute(f"alter table processes add column {name} {ddl}")

        mapping_columns = {str(row["name"]) for row in conn.execute("pragma table_info(mapping_records)").fetchall()}
        mapping_column_defs = {
            "style_no": "text not null default ''",
            "process_id": "text not null default ''",
            "suggested_new_skill": "integer not null default 0",
            "mes_efficiency": "real",
            "llm_process_id": "text not null default ''",
            "final_process_id": "text not null default ''",
        }
        for name, ddl in mapping_column_defs.items():
            if name not in mapping_columns:
                conn.execute(f"alter table mapping_records add column {name} {ddl}")

    def _seed_default_category(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert or ignore into categories(id, code, display_name, is_active)
                values (?, ?, ?, 1)
                """,
                (DEFAULT_CATEGORY_ID, DEFAULT_CATEGORY_ID, "裤子"),
            )

    def _migrate_legacy_employee_payload(self) -> None:
        with self.connect() as conn:
            existing = conn.execute("select count(*) as count from employee_skills").fetchone()
            if existing and int(existing["count"]) > 0:
                return
            # Older builds stored skill data inside employees.payload instead of the normalized employee_skills table.
            rows = conn.execute("select payload from employees").fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                employee = Employee(**payload)
                for index, (skill_name, efficiency) in enumerate(employee.skills.items()):
                    process = self.resolve_or_create_skill_process(
                        DEFAULT_CATEGORY_ID,
                        skill_name,
                        source="migration",
                        sort_order=index,
                        conn=conn,
                    )
                    conn.execute(
                        """
                        insert or replace into employee_skills(
                            employee_id, category_id, process_id, efficiency, source, notes
                        ) values (?, ?, ?, ?, ?, ?)
                        """,
                        (employee.id, DEFAULT_CATEGORY_ID, process.id, float(efficiency), "migration", "legacy payload migration"),
                    )

    def _backfill_process_columns(self) -> None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select id, payload from processes
                where style_no = '' or category_id = '' or process_no = '' or description = ''
                """
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                conn.execute(
                    """
                    update processes
                    set style_no = ?, category_id = ?, component = ?, process_no = ?, description = ?, sort_order = ?
                    where id = ?
                    """,
                    (
                        str(payload.get("style_no", "")),
                        str(payload.get("category_id", DEFAULT_CATEGORY_ID)),
                        str(payload.get("component", "")),
                        str(payload.get("process_no", "")),
                        str(payload.get("description", "")),
                        float(payload.get("sort_order", 0)),
                        str(row["id"]),
                    ),
                )

    def _backfill_mapping_style_columns(self) -> None:
        with self.connect() as conn:
            rows = conn.execute(
                "select process_hash, process_description, llm_skill_name, final_skill_name, confidence, human_approved, reason, suggested_new_skill, mes_efficiency, llm_process_id, final_process_id from mapping_records"
            ).fetchall()
            for row in rows:
                record = self._row_to_mapping(row)
                # mapping_knowledge is the shared memory reused across styles and future mapping runs.
                self._upsert_mapping_knowledge(conn, record)

    def _rebuild_mapping_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute("delete from mapping_fts")
        rows = conn.execute("select process_description, final_skill_name from mapping_knowledge").fetchall()
        for row in rows:
            conn.execute(
                "insert into mapping_fts(process_description, final_skill_name) values (?, ?)",
                (row["process_description"], row["final_skill_name"]),
            )

    def save_style_processes(self, processes: list[Process], category_id: str = DEFAULT_CATEGORY_ID) -> None:
        if not processes:
            return
        style_no = processes[0].style_no
        with self.connect() as conn:
            conn.execute("delete from processes where style_no = ? and category_id = ?", (style_no, category_id))
            conn.execute("delete from mapping_records where style_no = ?", (style_no,))
            for process in processes:
                conn.execute(
                    """
                    insert into processes(
                        id, style_no, category_id, component, process_no, description, sort_order, payload, version_hash
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        process.id,
                        process.style_no,
                        process.category_id,
                        process.component,
                        process.process_no,
                        process.description,
                        process.sort_order,
                        json.dumps(asdict(process), ensure_ascii=False),
                        process.version_hash,
                    ),
                )

    def replace_employee_import(
        self,
        employees: list[Employee],
        skill_processes: list[SkillProcess],
        employee_skills: list[EmployeeSkill],
        category_id: str = DEFAULT_CATEGORY_ID,
    ) -> None:
        with self.connect() as conn:
            conn.execute("delete from employees")
            conn.execute("delete from employee_skills where category_id = ?", (category_id,))
            for skill_process in skill_processes:
                existing = conn.execute(
                    "select aliases from skill_processes where id = ?",
                    (skill_process.id,),
                ).fetchone()
                aliases = skill_process.aliases
                if existing:
                    # Preserve previously learned aliases when users re-import an updated skill matrix.
                    aliases = _merge_aliases(list(json.loads(existing["aliases"] or "[]")), skill_process.aliases)
                conn.execute(
                    """
                    insert into skill_processes(
                        id, category_id, display_name, aliases, source, sort_order, is_active
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    on conflict(id) do update set
                        display_name=excluded.display_name,
                        aliases=excluded.aliases,
                        source=excluded.source,
                        sort_order=excluded.sort_order,
                        is_active=excluded.is_active
                    """,
                    (
                        skill_process.id,
                        skill_process.category_id,
                        skill_process.display_name,
                        json.dumps(aliases, ensure_ascii=False),
                        skill_process.source,
                        skill_process.sort_order,
                        int(skill_process.is_active),
                    ),
                )
            for employee in employees:
                conn.execute(
                    "insert into employees(id, payload) values (?, ?)",
                    (employee.id, json.dumps(asdict(employee), ensure_ascii=False)),
                )
            for employee_skill in employee_skills:
                conn.execute(
                    """
                    insert or replace into employee_skills(
                        employee_id, category_id, process_id, efficiency, source, notes
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        employee_skill.employee_id,
                        employee_skill.category_id,
                        employee_skill.process_id,
                        employee_skill.efficiency,
                        employee_skill.source,
                        employee_skill.notes,
                    ),
                )

    def save_import(
        self,
        processes: list[Process],
        employees: list[Employee],
        skill_processes: list[SkillProcess],
        employee_skills: list[EmployeeSkill],
        category_id: str = DEFAULT_CATEGORY_ID,
    ) -> None:
        if processes:
            self.save_style_processes(processes, category_id=category_id)
        self.replace_employee_import(employees, skill_processes, employee_skills, category_id=category_id)

    def list_styles(self, category_id: str = DEFAULT_CATEGORY_ID) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select distinct style_no
                from processes
                where category_id = ? and style_no <> ''
                order by style_no
                """,
                (category_id,),
            ).fetchall()
        return [str(row["style_no"]) for row in rows]

    def load_style_processes(self, style_no: str, category_id: str = DEFAULT_CATEGORY_ID) -> list[Process]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select payload from processes
                where style_no = ? and category_id = ?
                order by sort_order, process_no, id
                """,
                (style_no, category_id),
            ).fetchall()
        return [Process(**json.loads(row["payload"])) for row in rows]

    def save_mapping(self, record: MappingRecord, *, style_no: str = "", process_id: str = "") -> None:
        with self.connect() as conn:
            self._save_mapping(conn, record, style_no=style_no, process_id=process_id)
            self._rebuild_mapping_fts(conn)

    def save_style_mappings(self, style_no: str, processes: list[Process], mappings: list[MappingRecord]) -> None:
        by_hash = {process.identity_hash: process.id for process in processes}
        with self.connect() as conn:
            # A manual review session produces a full replacement snapshot for one style.
            conn.execute("delete from mapping_records where style_no = ?", (style_no,))
            for record in mappings:
                self._save_mapping(conn, record, style_no=style_no, process_id=by_hash.get(record.process_hash, ""))
            self._rebuild_mapping_fts(conn)

    def upsert_style_mappings(self, style_no: str, processes: list[Process], mappings: list[MappingRecord]) -> None:
        by_hash = {process.identity_hash: process.id for process in processes}
        with self.connect() as conn:
            for record in mappings:
                self._save_mapping(conn, record, style_no=style_no, process_id=by_hash.get(record.process_hash, ""))
            self._rebuild_mapping_fts(conn)

    def _save_mapping(self, conn: sqlite3.Connection, record: MappingRecord, *, style_no: str = "", process_id: str = "") -> None:
        params = (
            style_no,
            process_id,
            record.process_description,
            record.llm_skill_name,
            record.final_skill_name,
            record.confidence,
            int(record.human_approved),
            record.reason,
            int(record.suggested_new_skill),
            record.mes_efficiency,
            record.llm_process_id,
            record.final_process_id,
            record.process_hash,
        )
        updated = conn.execute(
            """
            update mapping_records
            set style_no = ?,
                process_id = ?,
                process_description = ?,
                llm_skill_name = ?,
                final_skill_name = ?,
                confidence = ?,
                human_approved = ?,
                reason = ?,
                suggested_new_skill = ?,
                mes_efficiency = ?,
                llm_process_id = ?,
                final_process_id = ?
            where process_hash = ?
            """,
            params,
        )
        if updated.rowcount == 0:
            conn.execute(
                """
                insert into mapping_records(
                    process_hash, style_no, process_id, process_description, llm_skill_name, final_skill_name,
                    confidence, human_approved, reason, suggested_new_skill, mes_efficiency, llm_process_id, final_process_id
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.process_hash,
                    style_no,
                    process_id,
                    record.process_description,
                    record.llm_skill_name,
                    record.final_skill_name,
                    record.confidence,
                    int(record.human_approved),
                    record.reason,
                    int(record.suggested_new_skill),
                    record.mes_efficiency,
                    record.llm_process_id,
                    record.final_process_id,
                ),
            )
        self._upsert_mapping_knowledge(conn, record)
    def _upsert_mapping_knowledge(self, conn: sqlite3.Connection, record: MappingRecord) -> None:
        normalized_description = normalize_text(record.process_description)
        if not normalized_description:
            return
        existing = conn.execute(
            "select * from mapping_knowledge where normalized_description = ?",
            (normalized_description,),
        ).fetchone()
        if existing and not _prefer_mapping(record, self._row_to_mapping(existing)):
            return
        # Store only the best known answer per normalized description to avoid conflicting historical hints.
        conn.execute(
            """
            insert into mapping_knowledge(
                normalized_description, process_description, llm_skill_name, final_skill_name,
                confidence, human_approved, reason, suggested_new_skill, mes_efficiency,
                llm_process_id, final_process_id, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
            on conflict(normalized_description) do update set
                process_description=excluded.process_description,
                llm_skill_name=excluded.llm_skill_name,
                final_skill_name=excluded.final_skill_name,
                confidence=excluded.confidence,
                human_approved=excluded.human_approved,
                reason=excluded.reason,
                suggested_new_skill=excluded.suggested_new_skill,
                mes_efficiency=excluded.mes_efficiency,
                llm_process_id=excluded.llm_process_id,
                final_process_id=excluded.final_process_id,
                updated_at=current_timestamp
            """,
            (
                normalized_description,
                record.process_description,
                record.llm_skill_name,
                record.final_skill_name,
                record.confidence,
                int(record.human_approved),
                record.reason,
                int(record.suggested_new_skill),
                record.mes_efficiency,
                record.llm_process_id,
                record.final_process_id,
            ),
        )

    def get_mapping(self, process_hash: str) -> MappingRecord | None:
        with self.connect() as conn:
            row = conn.execute("select * from mapping_records where process_hash = ?", (process_hash,)).fetchone()
        return self._row_to_mapping(row) if row else None

    def get_style_mapping(self, style_no: str, process_hash: str, require_reason: bool = False) -> MappingRecord | None:
        sql = """
            select * from mapping_records
            where style_no = ? and process_hash = ?
        """
        if require_reason:
            sql += " and trim(coalesce(reason, '')) <> ''"
        with self.connect() as conn:
            row = conn.execute(sql, (style_no, process_hash)).fetchone()
        return self._row_to_mapping(row) if row else None

    def find_mapping_knowledge(self, description: str) -> MappingRecord | None:
        normalized = normalize_text(description)
        if not normalized:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "select * from mapping_knowledge where normalized_description = ?",
                (normalized,),
            ).fetchone()
        return self._row_to_mapping(row) if row else None

    def search_mappings(self, query: str, limit: int = 5) -> list[MappingRecord]:
        query = " ".join(str(query).split())
        if not query:
            return []
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    select k.* from mapping_fts f
                    join mapping_knowledge k
                      on k.process_description = f.process_description
                     and k.final_skill_name = f.final_skill_name
                    where mapping_fts match ?
                    limit ?
                    """,
                    (_fts_phrase(query), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    select * from mapping_knowledge
                    where process_description like ?
                    limit ?
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def list_style_mappings(self, style_no: str) -> list[MappingRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from mapping_records
                where style_no = ?
                order by confidence desc, process_description asc
                """,
                (style_no,),
            ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def list_style_pending_mappings(self, style_no: str) -> list[MappingRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from mapping_records
                where style_no = ?
                  and (final_process_id = '' or human_approved = 0 or suggested_new_skill = 1)
                order by confidence asc, process_description asc
                """,
                (style_no,),
            ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def list_pending_mappings(self) -> list[MappingRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from mapping_records
                where final_process_id = '' or human_approved = 0 or suggested_new_skill = 1
                order by confidence asc, process_description asc
                """
            ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def list_skill_processes(
        self,
        category_id: str = DEFAULT_CATEGORY_ID,
        include_inactive: bool = True,
        conn: sqlite3.Connection | None = None,
    ) -> list[SkillProcess]:
        sql = """
            select * from skill_processes
            where category_id = ?
        """
        params: list[Any] = [category_id]
        if not include_inactive:
            sql += " and is_active = 1"
        sql += " order by sort_order, display_name"
        if conn is None:
            with self.connect() as managed_conn:
                rows = managed_conn.execute(sql, params).fetchall()
        else:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_skill_process(row) for row in rows]

    def get_skill_process(self, process_id: str) -> SkillProcess | None:
        with self.connect() as conn:
            row = conn.execute("select * from skill_processes where id = ?", (process_id,)).fetchone()
        return self._row_to_skill_process(row) if row else None

    def resolve_or_create_skill_process(
        self,
        category_id: str,
        display_name: str,
        source: str = "manual",
        sort_order: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> SkillProcess:
        existing = self.match_skill_process_by_name(category_id, display_name, conn=conn)
        if existing:
            return existing
        process = SkillProcess(
            id=skill_process_id(category_id, display_name),
            category_id=category_id,
            display_name=display_name.strip(),
            aliases=[display_name.strip()],
            source=source,
            sort_order=sort_order or 9999,
            is_active=True,
        )
        self.upsert_skill_process(process, conn=conn)
        return process

    def upsert_skill_process(self, process: SkillProcess, conn: sqlite3.Connection | None = None) -> None:
        if conn is None:
            with self.connect() as managed_conn:
                self.upsert_skill_process(process, conn=managed_conn)
            return
        conn.execute(
            """
            insert into skill_processes(
                id, category_id, display_name, aliases, source, sort_order, is_active
            ) values (?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                display_name=excluded.display_name,
                aliases=excluded.aliases,
                source=excluded.source,
                sort_order=excluded.sort_order,
                is_active=excluded.is_active
            """,
            (
                process.id,
                process.category_id,
                process.display_name,
                json.dumps(process.aliases, ensure_ascii=False),
                process.source,
                process.sort_order,
                int(process.is_active),
            ),
        )

    def match_skill_process_by_name(
        self,
        category_id: str,
        name: str,
        conn: sqlite3.Connection | None = None,
    ) -> SkillProcess | None:
        target = normalize_text(name)
        if not target:
            return None
        for process in self.list_skill_processes(category_id, include_inactive=True, conn=conn):
            if normalize_text(process.display_name) == target:
                return process
            if any(normalize_text(alias) == target for alias in process.aliases):
                return process
        return None

    def load_employees_with_skills(self, category_id: str = DEFAULT_CATEGORY_ID) -> list[Employee]:
        with self.connect() as conn:
            employee_rows = conn.execute("select * from employees order by id").fetchall()
            skill_rows = conn.execute(
                "select * from employee_skills where category_id = ? order by employee_id, process_id",
                (category_id,),
            ).fetchall()
        skill_map: dict[str, dict[str, float]] = {}
        for row in skill_rows:
            skill_map.setdefault(str(row["employee_id"]), {})[str(row["process_id"])] = float(row["efficiency"])
        employees: list[Employee] = []
        for row in employee_rows:
            payload = json.loads(row["payload"])
            employees.append(
                Employee(
                    id=str(payload["id"]),
                    name=str(payload["name"]),
                    role=str(payload.get("role", "")),
                    skills=skill_map.get(str(payload["id"]), {}),
                )
            )
        return employees

    def list_employee_skills(self, employee_id: str, category_id: str = DEFAULT_CATEGORY_ID) -> list[EmployeeSkill]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from employee_skills
                where employee_id = ? and category_id = ?
                order by process_id
                """,
                (employee_id, category_id),
            ).fetchall()
        return [self._row_to_employee_skill(row) for row in rows]

    def list_uncovered_skill_processes(self, category_id: str = DEFAULT_CATEGORY_ID) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                    sp.id,
                    sp.display_name,
                    sp.source,
                    count(case when es.efficiency > 0 then 1 end) as employee_count,
                    max(es.updated_at) as last_skill_update
                from skill_processes sp
                left join employee_skills es
                  on es.category_id = sp.category_id
                 and es.process_id = sp.id
                where sp.category_id = ?
                  and sp.is_active = 1
                group by sp.id, sp.display_name, sp.source
                having count(case when es.efficiency > 0 then 1 end) = 0
                order by
                    case when sp.source = 'mapping' then 0 else 1 end,
                    sp.sort_order,
                    sp.display_name
                """,
                (category_id,),
            ).fetchall()
        # This drives the "skills still missing coverage" UI and the pre-balancing guardrail.
        return [
            {
                "process_id": str(row["id"]),
                "display_name": str(row["display_name"]),
                "source": str(row["source"]),
                "employee_count": int(row["employee_count"] or 0),
                "last_skill_update": str(row["last_skill_update"] or ""),
            }
            for row in rows
        ]

    def upsert_employee_skill(self, employee_skill: EmployeeSkill) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into employee_skills(
                    employee_id, category_id, process_id, efficiency, source, notes
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(employee_id, category_id, process_id) do update set
                    efficiency=excluded.efficiency,
                    source=excluded.source,
                    notes=excluded.notes,
                    updated_at=current_timestamp
                """,
                (
                    employee_skill.employee_id,
                    employee_skill.category_id,
                    employee_skill.process_id,
                    employee_skill.efficiency,
                    employee_skill.source,
                    employee_skill.notes,
                ),
            )

    def delete_employee_skill(self, employee_id: str, category_id: str, process_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "delete from employee_skills where employee_id = ? and category_id = ? and process_id = ?",
                (employee_id, category_id, process_id),
            )

    def save_layout_result(self, result: AssignmentResult, mapping: dict[str, str], total_distance: float) -> None:
        payload: dict[str, Any] = {
            "mapping": mapping,
            "stations": [asdict(station) for station in result.stations],
            "metrics": asdict(result.metrics),
            "total_distance": total_distance,
            "warnings": result.warnings,
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into layout_state(id, payload, updated_at) values (1, ?, current_timestamp)
                on conflict(id) do update set payload=excluded.payload, updated_at=current_timestamp
                """,
                (json.dumps(payload, ensure_ascii=False),),
            )

    def load_layout_result(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select payload from layout_state where id = 1").fetchone()
        return json.loads(row["payload"]) if row else None

    @staticmethod
    def _row_to_mapping(row: sqlite3.Row | None) -> MappingRecord:
        if row is None:
            raise ValueError("row is required")
        return MappingRecord(
            process_hash=str(row["process_hash"]) if "process_hash" in row.keys() else "",
            process_description=str(row["process_description"]),
            llm_skill_name=str(row["llm_skill_name"]),
            final_skill_name=str(row["final_skill_name"]),
            confidence=float(row["confidence"]),
            human_approved=bool(row["human_approved"]),
            reason=str(row["reason"]),
            suggested_new_skill=bool(row["suggested_new_skill"]),
            mes_efficiency=row["mes_efficiency"] if "mes_efficiency" in row.keys() else None,
            llm_process_id=str(row["llm_process_id"] or "") if "llm_process_id" in row.keys() else "",
            final_process_id=str(row["final_process_id"] or "") if "final_process_id" in row.keys() else "",
        )

    @staticmethod
    def _row_to_skill_process(row: sqlite3.Row) -> SkillProcess:
        return SkillProcess(
            id=str(row["id"]),
            category_id=str(row["category_id"]),
            display_name=str(row["display_name"]),
            aliases=list(json.loads(row["aliases"] or "[]")),
            source=str(row["source"]),
            sort_order=int(row["sort_order"]),
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _row_to_employee_skill(row: sqlite3.Row) -> EmployeeSkill:
        return EmployeeSkill(
            employee_id=str(row["employee_id"]),
            category_id=str(row["category_id"]),
            process_id=str(row["process_id"]),
            efficiency=float(row["efficiency"]),
            source=str(row["source"]),
            updated_at=str(row["updated_at"] or ""),
            notes=str(row["notes"] or ""),
        )


def _fts_phrase(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def _merge_aliases(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for alias in [*existing, *incoming]:
        value = alias.strip()
        if value and value not in merged:
            merged.append(value)
    return merged


def _mapping_score(record: MappingRecord) -> tuple[int, int, int, float]:
    return (
        1 if record.human_approved else 0,
        1 if record.final_process_id else 0,
        0 if record.suggested_new_skill else 1,
        record.confidence,
    )


def _prefer_mapping(candidate: MappingRecord, existing: MappingRecord) -> bool:
    # Prefer confirmed mappings first, then concrete process matches, then non-new-skill suggestions, then confidence.
    return _mapping_score(candidate) >= _mapping_score(existing)
