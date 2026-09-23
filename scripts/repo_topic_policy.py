#!/usr/bin/env python3
"""GSB semantic-topic policy for prompt capacity and duplicate control."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TOPIC_MAP_FILENAME = "repo-topic-map.json"
SUPPORTED_PROFILES = {"gsb-semantic-v2"}
MAX_TOPIC_SEATS = 3


class TopicMapError(ValueError):
    """Raised when a repo topic map is missing required data."""


def resolve_topic_map_path(
    *,
    repo: Path | None = None,
    parent: Path | None = None,
    explicit: str | Path | None = None,
) -> Path | None:
    """Resolve an explicit topic map or discover the project-level default."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    if parent:
        candidate = Path(parent).expanduser().resolve() / TOPIC_MAP_FILENAME
        if candidate.is_file():
            return candidate
    if repo:
        repo_path = Path(repo).expanduser().resolve()
        roots = [repo_path, repo_path.parent, repo_path.parent.parent]
        for root in roots:
            candidate = root / TOPIC_MAP_FILENAME
            if candidate.is_file():
                return candidate
    return None


def _require_text(item: dict[str, Any], key: str, *, where: str) -> str:
    value = str(item.get(key) or "").strip()
    if not value:
        raise TopicMapError(f"{where} 缺少 {key}")
    return value


def _require_text_list(item: dict[str, Any], key: str, *, where: str) -> list[str]:
    raw = item.get(key)
    if not isinstance(raw, list):
        raise TopicMapError(f"{where} 的 {key} 必须是数组")
    values = [str(value).strip() for value in raw if str(value).strip()]
    if not values:
        raise TopicMapError(f"{where} 的 {key} 不能为空")
    return values


def normalize_topic_map(payload: dict[str, Any], *, source: Path | None = None) -> dict[str, Any]:
    """Validate and normalize a topic map without changing its business meaning."""
    if not isinstance(payload, dict):
        raise TopicMapError("topic map 顶层必须是对象")
    version = int(payload.get("version") or 0)
    if version != 1:
        raise TopicMapError(f"不支持 topic map version={version}，当前只支持 version=1")
    profile = str(payload.get("profile") or "").strip()
    if profile not in SUPPORTED_PROFILES:
        raise TopicMapError(f"不支持 profile={profile or '<empty>'}，允许值：{sorted(SUPPORTED_PROFILES)}")
    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        raise TopicMapError("topics 必须是非空数组")

    topics: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for index, raw in enumerate(raw_topics):
        where = f"topics[{index}]"
        if not isinstance(raw, dict):
            raise TopicMapError(f"{where} 必须是对象")
        topic_id = _require_text(raw, "id", where=where)
        topic_name = _require_text(raw, "name", where=where)
        if topic_id in seen_ids:
            raise TopicMapError(f"topic id 重复：{topic_id}")
        if topic_name in seen_names:
            raise TopicMapError(f"topic name 重复：{topic_name}")
        seen_ids.add(topic_id)
        seen_names.add(topic_name)

        keywords = _require_text_list(raw, "keywords", where=where)
        evidence = _require_text_list(raw, "evidence", where=where)
        try:
            seat_capacity = int(raw.get("seat_capacity"))
        except (TypeError, ValueError):
            raise TopicMapError(f"{where} 的 seat_capacity 必须是整数") from None
        if not 1 <= seat_capacity <= MAX_TOPIC_SEATS:
            raise TopicMapError(
                f"{where} 的 seat_capacity={seat_capacity}，必须在 1 到 {MAX_TOPIC_SEATS} 之间"
            )
        allowed_families = raw.get("allowed_families") or []
        if not isinstance(allowed_families, list):
            raise TopicMapError(f"{where} 的 allowed_families 必须是数组")
        topics.append(
            {
                "id": topic_id,
                "name": topic_name,
                "module": str(raw.get("module") or "").strip(),
                "keywords": sorted(set(keywords), key=keywords.index),
                "evidence": evidence,
                "seat_capacity": seat_capacity,
                "allowed_families": [str(value).strip() for value in allowed_families if str(value).strip()],
                "notes": str(raw.get("notes") or "").strip(),
            }
        )

    max_capacity = payload.get("max_capacity")
    if max_capacity is not None:
        try:
            max_capacity = int(max_capacity)
        except (TypeError, ValueError):
            raise TopicMapError("max_capacity 必须是整数") from None
        if max_capacity <= 0:
            raise TopicMapError("max_capacity 必须大于 0")

    topic_ids = {topic["id"] for topic in topics}
    raw_plan = payload.get("task_plan") or []
    if not isinstance(raw_plan, list):
        raise TopicMapError("task_plan 必须是数组")
    task_plan: list[dict[str, Any]] = []
    seen_sequences: set[int] = set()
    for index, raw in enumerate(raw_plan):
        where = f"task_plan[{index}]"
        if not isinstance(raw, dict):
            raise TopicMapError(f"{where} 必须是对象")
        try:
            sequence = int(raw.get("sequence"))
        except (TypeError, ValueError):
            raise TopicMapError(f"{where} 的 sequence 必须是整数") from None
        if sequence <= 0:
            raise TopicMapError(f"{where} 的 sequence 必须大于 0")
        if sequence in seen_sequences:
            raise TopicMapError(f"task_plan sequence 重复：{sequence}")
        seen_sequences.add(sequence)
        slug = _require_text(raw, "slug", where=where)
        task_type = _require_text(raw, "task_type", where=where)
        topic = _require_text(raw, "topic", where=where)
        if topic not in topic_ids:
            raise TopicMapError(f"{where} 引用了不存在的 topic：{topic}")
        family = str(raw.get("family") or "").strip()
        task_plan.append(
            {
                "sequence": sequence,
                "slug": slug,
                "task_type": task_type,
                "topic": topic,
                "family": family,
                "angle": str(raw.get("angle") or "").strip(),
            }
        )
    task_plan.sort(key=lambda item: int(item["sequence"]))

    normalized = {
        "version": version,
        "profile": profile,
        "repo": str(payload.get("repo") or "").strip(),
        "base_commit": str(payload.get("base_commit") or "").strip(),
        "max_capacity": max_capacity,
        "topics": topics,
        "task_plan": task_plan,
    }
    if source is not None:
        normalized["source"] = str(source)
    return normalized


def load_topic_map(
    *,
    repo: Path | None = None,
    parent: Path | None = None,
    explicit: str | Path | None = None,
    required: bool = False,
    expected_base_commit: str = "",
) -> dict[str, Any] | None:
    """Load a topic map, optionally enforcing presence and base-commit identity."""
    path = resolve_topic_map_path(repo=repo, parent=parent, explicit=explicit)
    if path is None:
        if required:
            raise TopicMapError(
                f"缺少 {TOPIC_MAP_FILENAME}；GSB semantic-v2 批次必须先提供业务 topic map"
            )
        return None
    if not path.is_file():
        raise TopicMapError(f"topic map 不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopicMapError(f"读取 topic map 失败：{path}: {exc}") from exc
    normalized = normalize_topic_map(payload, source=path)
    if expected_base_commit and normalized.get("base_commit") and normalized["base_commit"] != expected_base_commit:
        raise TopicMapError(
            f"topic map 基准提交与当前仓库不一致：map={normalized['base_commit']} "
            f"repo={expected_base_commit}"
        )
    return normalized


def detect_topic(text: str, topic_map: dict[str, Any] | None) -> dict[str, Any]:
    """Return the best semantic topic, rejecting ties between distinct topics."""
    if not topic_map:
        return {}
    scores: list[tuple[int, dict[str, Any], list[str]]] = []
    prompt = str(text or "")
    for topic in topic_map.get("topics", []):
        matched = [keyword for keyword in topic.get("keywords", []) if keyword in prompt]
        score = sum(prompt.count(keyword) for keyword in matched)
        if score > 0:
            scores.append((score, topic, matched))
    if not scores:
        return {}
    scores.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    best_score = scores[0][0]
    winners = [item for item in scores if item[0] == best_score]
    if len(winners) > 1:
        return {
            "topic_ambiguous": [str(item[1].get("id") or "") for item in winners],
        }
    _, topic, matched = winners[0]
    return {
        "topic": str(topic.get("id") or ""),
        "topic_name": str(topic.get("name") or ""),
        "topic_module": str(topic.get("module") or ""),
        "topic_score": best_score,
        "topic_keywords": matched,
        "topic_seat_capacity": int(topic.get("seat_capacity") or 1),
        "topic_allowed_families": list(topic.get("allowed_families") or []),
    }


def topic_slots(topic_map: dict[str, Any] | None) -> int:
    if not topic_map:
        return 0
    return sum(int(topic.get("seat_capacity") or 0) for topic in topic_map.get("topics", []))


def topic_seat_limits(topic_map: dict[str, Any] | None) -> dict[str, int]:
    if not topic_map:
        return {}
    return {
        str(topic.get("id") or ""): int(topic.get("seat_capacity") or 0)
        for topic in topic_map.get("topics", [])
    }


def topic_module_limits(topic_map: dict[str, Any] | None) -> dict[str, int]:
    if not topic_map:
        return {}
    limits: dict[str, int] = {}
    for topic in topic_map.get("topics", []):
        module = str(topic.get("module") or "").strip()
        if not module:
            continue
        limits[module] = limits.get(module, 0) + int(topic.get("seat_capacity") or 0)
    return limits
