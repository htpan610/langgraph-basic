from __future__ import annotations

from collections import Counter

from core.config import load_settings
from core.db import Repository
from core.mapper import is_human_confirmed
from data.models import DEFAULT_CATEGORY_ID


def main() -> None:
    settings = load_settings()
    repository = Repository(settings.app.database_path)

    styles = repository.list_styles(DEFAULT_CATEGORY_ID)
    print(f"styles={len(styles)}")
    if styles:
        print("style_list=", styles)

    uncovered = repository.list_uncovered_skill_processes(DEFAULT_CATEGORY_ID)
    print(f"uncovered_skill_processes={len(uncovered)}")
    for item in uncovered[:10]:
        print("  uncovered:", item["display_name"], item["source"])

    for style_no in styles:
        processes = repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID)
        mappings = repository.list_style_mappings(style_no)
        missing_process_ids = sum(1 for item in mappings if not item.final_process_id)
        pending_review = sum(1 for item in mappings if not item.human_approved or item.suggested_new_skill)
        print(
            f"style={style_no} processes={len(processes)} mappings={len(mappings)} "
            f"missing_process_ids={missing_process_ids} pending_review={pending_review} "
            f"human_confirmed={is_human_confirmed(mappings, settings.llm.confidence_threshold)}"
        )
        final_ids = [item.final_process_id for item in mappings if item.final_process_id]
        if final_ids:
            top = Counter(final_ids).most_common(5)
            print("  top_process_ids=", top)


if __name__ == "__main__":
    main()
