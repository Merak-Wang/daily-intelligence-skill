from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

_LATIN_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]*")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "its",
    "new",
    "of",
    "on",
    "or",
    "says",
    "the",
    "to",
    "with",
}
_SEVERITY_TERMS = {
    "attack",
    "ban",
    "bankruptcy",
    "ceasefire",
    "crash",
    "crisis",
    "emergency",
    "explosion",
    "invasion",
    "launch",
    "lawsuit",
    "outage",
    "recall",
    "sanction",
    "war",
    "下调",
    "制裁",
    "危机",
    "召回",
    "战争",
    "爆炸",
    "禁令",
    "袭击",
}


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def _normalized_title(value: str) -> str:
    return " ".join(
        re.sub(r"[^\w\u3400-\u9fff]+", " ", (value or "").casefold()).split()
    )


def lexical_tokens(value: str) -> list[str]:
    normalized = _normalized_title(value)
    tokens = [
        token
        for token in _LATIN_TOKEN.findall(normalized)
        if len(token) > 1 and token not in _STOPWORDS
    ]
    for run in _CJK_RUN.findall(normalized):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _hash_feature(feature: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    number = int.from_bytes(digest, "big")
    return number % dimensions, -1.0 if number & (1 << 63) else 1.0


def lexical_vector(value: str, dimensions: int = 512) -> dict[int, float]:
    tokens = lexical_tokens(value)
    features = [f"u:{token}" for token in tokens]
    features.extend(
        f"b:{left}|{right}" for left, right in zip(tokens, tokens[1:], strict=False)
    )
    compact = re.sub(r"\s+", "", _normalized_title(value))
    features.extend(
        f"c:{compact[index:index + 3]}"
        for index in range(max(0, len(compact) - 2))
    )
    vector: defaultdict[int, float] = defaultdict(float)
    for feature in features:
        position, sign = _hash_feature(feature, dimensions)
        vector[position] += sign
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    if not norm:
        return {}
    return {position: weight / norm for position, weight in vector.items()}


def cosine_similarity(left: dict[int, float], right: dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(weight * right.get(position, 0.0) for position, weight in left.items())


def _parse_time(item: dict[str, Any]) -> datetime | None:
    value = item.get("published_at") or item.get("discovered_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _containment_match(left: str, right: str) -> bool:
    left_compact = re.sub(r"\s+", "", _normalized_title(left))
    right_compact = re.sub(r"\s+", "", _normalized_title(right))
    shorter, longer = sorted((left_compact, right_compact), key=len)
    return len(shorter) >= 14 and shorter in longer


def _role_score(item: dict[str, Any]) -> int:
    metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
    role = str(metadata.get("role") or "discovery")
    return {"primary": 10, "evidence": 8, "corroboration": 7, "discovery": 4}.get(
        role, 4
    )


def _tier(item: dict[str, Any]) -> int:
    metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
    try:
        return min(3, max(1, int(metadata.get("tier", 2))))
    except (TypeError, ValueError):
        return 2


def _representative(items: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        items,
        key=lambda item: (
            _tier(item),
            -_role_score(item),
            -len(str(item.get("title") or "")),
            str(item.get("item_id") or ""),
        ),
    )


def _cluster_importance(
    items: list[dict[str, Any]],
    generated_at: datetime,
    prior_item_ids: set[str],
) -> int:
    representative = _representative(items)
    tier_score = {1: 25, 2: 18, 3: 12}[_tier(representative)]
    role_score = _role_score(representative)
    sources = {str(item.get("source_id") or "") for item in items}
    corroboration = min(20, max(0, len(sources) - 1) * 7)
    published = max(
        (parsed for item in items if (parsed := _parse_time(item)) is not None),
        default=None,
    )
    age_hours = (
        max(0.0, (generated_at.astimezone(UTC) - published).total_seconds() / 3600)
        if published is not None
        else 168.0
    )
    recency = round(20 * max(0.0, 1.0 - min(age_hours, 168.0) / 168.0))
    title_tokens = set(lexical_tokens(" ".join(str(item.get("title") or "") for item in items)))
    severity = min(15, 5 * len(title_tokens & _SEVERITY_TERMS))
    novelty = 10 if not any(str(item.get("item_id")) in prior_item_ids for item in items) else 3
    return min(100, tier_score + role_score + corroboration + recency + severity + novelty)


def cluster_articles(
    items: list[dict[str, Any]],
    generated_at: str,
    *,
    threshold: float = 0.68,
    previous_clusters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Cluster same-story headlines with deterministic, model-free lexical features."""
    if not items:
        return []
    generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    generated = generated.replace(tzinfo=generated.tzinfo or UTC)
    vectors = [lexical_vector(str(item.get("title") or "")) for item in items]
    token_sets = [set(lexical_tokens(str(item.get("title") or ""))) for item in items]
    canonical_to_index: dict[str, int] = {}
    inverted: defaultdict[str, list[int]] = defaultdict(list)
    pairs: set[tuple[int, int]] = set()
    for index, item in enumerate(items):
        canonical = str(item.get("canonical_url") or "")
        if canonical and canonical in canonical_to_index:
            pairs.add((canonical_to_index[canonical], index))
        elif canonical:
            canonical_to_index[canonical] = index
        candidates: set[int] = set()
        for token in sorted(token_sets[index]):
            candidates.update(inverted[token][-80:])
        for candidate in candidates:
            pairs.add((candidate, index))
        for token in token_sets[index]:
            inverted[token].append(index)

    union = _UnionFind(len(items))
    for left, right in sorted(pairs):
        left_item = items[left]
        right_item = items[right]
        if str(left_item.get("module")) != str(right_item.get("module")):
            continue
        left_time = _parse_time(left_item)
        right_time = _parse_time(right_item)
        if (
            left_time is not None
            and right_time is not None
            and abs((left_time - right_time).total_seconds()) > 96 * 3600
        ):
            continue
        canonical_match = (
            left_item.get("canonical_url")
            and left_item.get("canonical_url") == right_item.get("canonical_url")
        )
        shared_tokens = token_sets[left] & token_sets[right]
        if not canonical_match and len(shared_tokens) < 2:
            continue
        similarity = cosine_similarity(vectors[left], vectors[right])
        if canonical_match or similarity >= threshold or _containment_match(
            str(left_item.get("title") or ""),
            str(right_item.get("title") or ""),
        ):
            union.union(left, right)

    groups: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(items):
        groups[union.find(index)].append(item)

    previous_clusters = previous_clusters or []
    prior_story_by_item = {
        str(item_id): str(cluster.get("story_id"))
        for cluster in previous_clusters
        if isinstance(cluster, dict) and cluster.get("story_id")
        for item_id in cluster.get("item_ids", [])
    }
    prior_item_ids = set(prior_story_by_item)
    clusters: list[dict[str, Any]] = []
    for grouped_items in groups.values():
        representative = _representative(grouped_items)
        old_story_ids = sorted(
            {
                prior_story_by_item[str(item.get("item_id"))]
                for item in grouped_items
                if str(item.get("item_id")) in prior_story_by_item
            }
        )
        if old_story_ids:
            story_id = old_story_ids[0]
        else:
            identity = _normalized_title(str(representative.get("title") or ""))
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
            story_id = f"story-{digest}"
        source_ids = sorted(
            {str(item.get("source_id") or "") for item in grouped_items if item.get("source_id")}
        )
        parsed_times = [
            parsed for item in grouped_items if (parsed := _parse_time(item)) is not None
        ]
        prior_count = sum(
            str(item.get("item_id")) in prior_item_ids for item in grouped_items
        )
        if len(source_ids) >= 3:
            phase = "confirmed"
        elif prior_count and prior_count < len(grouped_items):
            phase = "updated"
        elif len(source_ids) >= 2:
            phase = "developing"
        else:
            phase = "new"
        cluster = {
            "story_id": story_id,
            "title": representative.get("title"),
            "representative_item_id": representative.get("item_id"),
            "item_ids": [str(item.get("item_id")) for item in grouped_items],
            "source_ids": source_ids,
            "source_count": len(source_ids),
            "module": representative.get("module"),
            "category": representative.get("category"),
            "phase": phase,
            "importance": _cluster_importance(
                grouped_items, generated, prior_item_ids
            ),
            "first_seen_at": min(
                (str(item.get("discovered_at")) for item in grouped_items),
                default=generated_at,
            ),
            "last_seen_at": max(
                (str(item.get("discovered_at")) for item in grouped_items),
                default=generated_at,
            ),
            "published_at": (
                max(parsed_times).isoformat(timespec="seconds") if parsed_times else None
            ),
            "image_url": next(
                (
                    str(item.get("image_url"))
                    for item in grouped_items
                    if item.get("image_url")
                ),
                None,
            ),
        }
        for item in grouped_items:
            metadata = item.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["story_id"] = story_id
                metadata["story_phase"] = phase
                metadata["cluster_importance"] = cluster["importance"]
        clusters.append(cluster)
    clusters.sort(
        key=lambda cluster: (
            int(cluster["importance"]),
            str(cluster.get("published_at") or cluster.get("last_seen_at") or ""),
        ),
        reverse=True,
    )
    return clusters
