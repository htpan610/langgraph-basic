from __future__ import annotations

import hashlib
import re


def normalize_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", "", text.strip().lower())
    return text


def process_identity_hash(style_no: object, component: object, process_no: object, description: object) -> str:
    raw = "|".join(
        [
            normalize_text(style_no),
            normalize_text(component),
            normalize_text(process_no),
            normalize_text(description),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def process_version_hash(
    identity_hash: str,
    standard_time: object,
    standard_price: object,
    sort_order: object,
) -> str:
    raw = "|".join(
        [
            identity_hash,
            normalize_text(standard_time),
            normalize_text(standard_price),
            normalize_text(sort_order),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def skill_process_id(category_id: str, display_name: object) -> str:
    raw = "|".join([normalize_text(category_id), normalize_text(display_name)])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{normalize_text(category_id) or 'category'}_{digest}"
