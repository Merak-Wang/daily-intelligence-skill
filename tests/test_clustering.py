from daily_intelligence.clustering import cluster_articles, cosine_similarity, lexical_vector


def _item(item_id: str, source_id: str, title: str, url: str) -> dict:
    return {
        "item_id": item_id,
        "source_id": source_id,
        "source_name": source_id,
        "title": title,
        "url": url,
        "canonical_url": url,
        "published_at": "2026-07-24T09:00:00+08:00",
        "discovered_at": "2026-07-24T09:10:00+08:00",
        "module": "information",
        "category": "international",
        "metadata": {"tier": 1, "role": "evidence"},
    }


def test_lexical_vectors_and_same_story_clustering_are_deterministic():
    left = "Central bank holds rates and signals patience on inflation"
    right = "Central bank holds interest rates, signals patience over inflation"

    assert cosine_similarity(lexical_vector(left), lexical_vector(right)) > 0.6

    items = [
        _item("a", "source-a", left, "https://a.example/story"),
        _item("b", "source-b", right, "https://b.example/story"),
        _item(
            "c",
            "source-c",
            "New telescope releases images of a distant stellar nursery",
            "https://c.example/space",
        ),
    ]
    clusters = cluster_articles(
        items,
        "2026-07-24T10:00:00+08:00",
        threshold=0.6,
    )

    assert sorted(cluster["source_count"] for cluster in clusters) == [1, 2]
    shared = next(cluster for cluster in clusters if cluster["source_count"] == 2)
    assert shared["phase"] == "developing"
    assert set(shared["item_ids"]) == {"a", "b"}


def test_story_id_survives_a_later_corroborating_article():
    initial = [
        _item(
            "a",
            "source-a",
            "Regional leaders agree a new maritime security framework",
            "https://a.example/framework",
        )
    ]
    first = cluster_articles(initial, "2026-07-24T10:00:00+08:00")
    later = [
        *initial,
        _item(
            "b",
            "source-b",
            "Regional leaders agree on new maritime security framework",
            "https://b.example/framework",
        ),
    ]
    second = cluster_articles(
        later,
        "2026-07-24T11:00:00+08:00",
        threshold=0.6,
        previous_clusters=first,
    )

    assert second[0]["story_id"] == first[0]["story_id"]
    assert second[0]["phase"] == "updated"

