from daily_intelligence.adapters import (
    _published_at,
    arxiv_items_from_rows,
    collect_browser_index,
    latest_year_url,
    seed_items_from_payload,
    twz_items_from_rows,
)
from daily_intelligence.collector import classify_source_status, is_eligible
from daily_intelligence.config import load_config
from daily_intelligence.models import SourceStatus
from daily_intelligence.utils import canonicalize_url


def test_tracking_parameters_are_removed():
    url = "https://www.yahoo.com/news/a.html?guccounter=1&utm_source=x&keep=yes#fragment"
    assert canonicalize_url(url) == "https://yahoo.com/news/a.html?keep=yes"


def test_cnbc_article_filter_accepts_article_and_rejects_quote():
    config = load_config()
    source = config.source_by_id("cnbc_world")
    assert is_eligible(
        source,
        "The AI race is shifting from bigger models to cheaper systems",
        "https://www.cnbc.com/2026/07/10/the-ai-race.html",
    )
    assert not is_eligible(
        source,
        "META 669.21 Meta Platforms",
        "https://www.cnbc.com/quotes/META",
    )


def test_twz_author_page_is_rejected():
    config = load_config()
    source = config.source_by_id("twz")
    assert not is_eligible(
        source,
        "JOSEPH TREVITHICK",
        "https://www.twz.com/authors/joseph-trevithick",
    )


def test_generic_navigation_and_numeric_titles_are_rejected():
    config = load_config()
    arxiv = config.source_by_id("arxiv_ai")
    github = config.source_by_id("github_trending")
    anthropic = config.source_by_id("anthropic_research")

    assert not is_eligible(arxiv, "Abstract", "https://arxiv.org/abs/2607.12345")
    assert not is_eligible(arxiv, "106391605", "https://arxiv.org/abs/2607.12345")
    assert not is_eligible(
        github, "Developers", "https://github.com/trending/developers"
    )
    assert not is_eligible(
        anthropic,
        "Alignment Science Team",
        "https://www.anthropic.com/research/team/alignment-science",
    )


def test_arxiv_adapter_uses_list_title_instead_of_abstract_link_label():
    source = load_config().source_by_id("arxiv_ai")
    rows = [
        {
            "title": "Title: Deep Interaction Learning for Reliable Agents",
            "href": "https://arxiv.org/abs/2607.12345",
            "date_text": "Thu, 16 Jul 2026",
        }
    ]

    items = arxiv_items_from_rows(rows, source, "2026-07-16T06:00:00+08:00")

    assert [item.title for item in items] == [
        "Deep Interaction Learning for Reliable Agents"
    ]
    assert items[0].published_at == "2026-07-16"


def test_hacker_news_keeps_external_story_and_rejects_internal_thread():
    source = load_config().source_by_id("hacker_news")
    assert is_eligible(source, "A useful agent engineering article", "https://example.com/agents")
    assert not is_eligible(
        source,
        "Comments for the story",
        "https://news.ycombinator.com/item?id=123",
    )


def test_research_and_repository_filters_are_specific():
    config = load_config()
    anthropic = config.source_by_id("anthropic_research")
    arxiv = config.source_by_id("arxiv_ai")
    github = config.source_by_id("github_trending")

    assert is_eligible(
        anthropic,
        "Measuring model alignment",
        "https://www.anthropic.com/research/measuring-model-alignment",
    )
    assert not is_eligible(
        anthropic,
        "Anthropic company news",
        "https://www.anthropic.com/news/company-update",
    )
    assert is_eligible(arxiv, "A new paper about agents", "https://arxiv.org/abs/2607.12345")
    assert is_eligible(github, "owner / useful-agent", "https://github.com/owner/useful-agent")
    assert not is_eligible(
        github,
        "AI repositories topic",
        "https://github.com/topics/artificial-intelligence",
    )


def test_new_finance_research_and_military_filters():
    config = load_config()
    yicai = config.source_by_id("yicai_economy")
    openai = config.source_by_id("openai_publications")
    deepmind = config.source_by_id("deepmind_publications")
    huggingface = config.source_by_id("huggingface_papers")
    papers_with_code = config.source_by_id("papers_with_code")
    defence_blog = config.source_by_id("defence_blog_aviation")
    aviationist = config.source_by_id("the_aviationist")

    assert is_eligible(yicai, "重磅经济数据即将发布", "https://www.yicai.com/news/103270952.html")
    assert not is_eligible(yicai, "全球市场大直播", "https://www.yicai.com/brief/103271044.html")
    assert is_eligible(
        openai, "A new reasoning research result", "https://openai.com/index/new-reasoning-result/"
    )
    assert is_eligible(
        deepmind,
        "A publication about agent learning",
        "https://deepmind.google/research/publications/agent-learning/",
    )
    assert is_eligible(
        huggingface, "A useful monthly paper", "https://huggingface.co/papers/2607.12345"
    )
    assert is_eligible(
        papers_with_code,
        "A useful paper with code",
        "https://huggingface.co/papers/2607.12345",
    )
    assert not is_eligible(
        papers_with_code,
        "Trending papers",
        "https://huggingface.co/papers/trending",
    )
    assert is_eligible(
        defence_blog,
        "New military aircraft enters service",
        "https://defence-blog.com/new-military-aircraft-enters-service/",
    )
    assert is_eligible(
        aviationist,
        "A detailed military aviation report",
        "https://theaviationist.com/2026/07/10/new-aircraft-report/",
    )


def test_seed_papers_adapter_uses_official_external_link():
    source = load_config().source_by_id("bytedance_seed_papers")
    payload = {
        "sub_article_list": [
            {
                "ArticleMeta": {
                    "ArticleID": 123,
                    "Author": "Research Team",
                    "Journal": "arXiv",
                    "PublishDate": 1_783_267_200_000,
                    "ExternalLinks": [
                        {"ExternalLinkType": 1, "Link": "https://arxiv.org/pdf/2607.05155.pdf"}
                    ],
                    "ResearchArea": [
                        {
                            "ResearchAreaName": "Artificial Intelligence",
                            "ResearchAreaNameZh": "人工智能",
                        }
                    ],
                },
                "ArticleSubContentEn": {
                    "Title": "EdgeBench: Learning from Real-World Environments",
                    "Abstract": "A benchmark for long-horizon agent learning.",
                },
                "ArticleSubContentZh": {"Title": "", "Abstract": ""},
            }
        ]
    }

    items = seed_items_from_payload(payload, source, "2026-07-12T00:00:00+08:00")

    assert len(items) == 1
    assert items[0].url == "https://arxiv.org/abs/2607.05155"
    assert items[0].description.startswith("A benchmark")
    assert items[0].metadata["research_areas"] == ["人工智能"]


def test_primary_economic_source_filters():
    config = load_config()
    nbs = config.source_by_id("nbs_china_releases")
    pboc = config.source_by_id("pboc_monetary_reports")
    sec = config.source_by_id("sec_edgar_latest")
    fed = config.source_by_id("federal_reserve_releases")

    assert is_eligible(
        nbs,
        "Consumer Price Index in May 2026",
        "https://www.stats.gov.cn/english/PressRelease/202606/t20260611_1963001.html",
    )
    assert not is_eligible(
        nbs,
        "Regular Press Release Calendar",
        "https://www.stats.gov.cn/english/PressRelease/ReleaseCalendar/202512/x.html",
    )
    assert is_eligible(
        pboc,
        "China Monetary Policy Report Q1 2025",
        "https://xining.pbc.gov.cn/en/3688229/3688353/3688356/2025/"
        "2025120609594943547/index.html",
    )
    assert is_eligible(
        sec,
        "Current report filing details",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000050/"
        "aapl-20260710-index.html",
    )
    assert is_eligible(
        fed,
        "Federal Reserve issues FOMC statement",
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm",
    )


def test_latest_year_index_selects_newest_year():
    rows = [
        {"title": "2024", "href": "https://example.test/2024/"},
        {"title": "Reports", "href": "https://example.test/reports/"},
        {"title": "2026", "href": "https://example.test/2026/"},
        {"title": "2025", "href": "https://example.test/2025/"},
    ]

    assert latest_year_url(rows) == "https://example.test/2026/"


def test_additional_research_news_and_military_filters():
    config = load_config()
    google = config.source_by_id("google_research_publications")
    microsoft = config.source_by_id("microsoft_research_publications")
    nvidia = config.source_by_id("nvidia_research_publications")
    lobsters = config.source_by_id("lobsters")
    infoq = config.source_by_id("infoq_ai")
    usni = config.source_by_id("usni_news")
    rusi = config.source_by_id("rusi_publications")

    assert is_eligible(
        google,
        "Visual Planning with Images",
        "https://research.google/pubs/visual-planning-lets-think-only-with-images/",
    )
    assert is_eligible(
        microsoft,
        "Serving Models Fast and Slow",
        "https://www.microsoft.com/en-us/research/publication/serving-models-fast-and-slow/",
    )
    assert is_eligible(
        nvidia,
        "Open World Task and Motion Planning",
        "https://research.nvidia.com/publication/2026-01_open-world-task-and-motion-planning-"
        "vision-language-model-inferred-constraints",
    )
    assert is_eligible(lobsters, "A useful systems article", "https://example.org/systems")
    assert not is_eligible(
        lobsters, "Internal Lobsters discussion", "https://lobste.rs/s/abcdef/internal-thread"
    )
    assert is_eligible(
        infoq,
        "Cloudflare details a new AI architecture",
        "https://www.infoq.com/news/2026/07/cloudflare-ai-architecture/",
    )
    assert is_eligible(
        usni,
        "New maritime operations report",
        "https://news.usni.org/2026/07/10/new-maritime-operations-report",
    )
    assert is_eligible(
        rusi,
        "Strategic analysis of European defence",
        "https://www.rusi.org/explore-our-research/publications/commentary/"
        "strategic-analysis-european-defence",
    )


def test_yahoo_comment_link_title_is_filtered():
    yahoo = load_config().source_by_id("yahoo_news")

    assert not is_eligible(
        yahoo,
        "View all comments",
        "https://www.yahoo.com/news/example-story-120000000.html",
    )


def test_twz_index_extracts_card_publication_date():
    source = load_config().source_by_id("twz")
    rows = [
        {
            "title": "Chinese J-15 Fighter Seen Launching From A Carrier",
            "href": "https://www.twz.com/sea/chinese-j-15-fighter-seen-launching-from-a-carrier",
            "card_text": "Chinese J-15 Fighter\nPosted on Jul 9, 2026",
            "description": "The carrier is unlocking the fighter's strike potential.",
        }
    ]

    items = twz_items_from_rows(rows, source, "2026-07-13T06:00:00+08:00")

    assert len(items) == 1
    assert items[0].published_at == "2026-07-09"
    assert items[0].description.startswith("The carrier")


def test_twz_duplicate_card_descriptions_are_dropped_and_relative_time_is_parsed():
    source = load_config().source_by_id("twz")
    duplicate = (
        "More than twenty warships are now operating in the region according to this "
        "long card description that must not be attached to unrelated stories."
    )
    rows = [
        {
            "title": "First military aviation development with enough title detail",
            "href": "https://www.twz.com/air/first-development",
            "card_text": "9 hours ago",
            "description": duplicate,
        },
        {
            "title": "Second unrelated naval development with enough title detail",
            "href": "https://www.twz.com/sea/second-development",
            "card_text": "9 hours ago",
            "description": duplicate,
        },
    ]

    items = twz_items_from_rows(rows, source, "2026-07-16T06:00:00+08:00")

    assert len(items) == 2
    assert {item.description for item in items} == {""}
    assert all(item.published_at == "2026-07-15T21:00:00+08:00" for item in items)


def test_generic_index_date_parser_uses_card_text_or_url():
    assert _published_at("Published July 13, 2026", "https://example.com/story") == "2026-07-13"
    assert _published_at("", "https://example.com/2026/07/10/story") == "2026-07-10"
    assert _published_at(
        "Published 2 hrs ago",
        "https://example.com/story",
        "2026-07-14T06:00:00+08:00",
    ) == "2026-07-14T04:00:00+08:00"


def test_generic_browser_adapter_persists_relative_publication_time():
    class Locator:
        def evaluate_all(self, _script):
            return [
                {
                    "title": "A sufficiently long BBC headline",
                    "href": "https://www.bbc.com/news/articles/example",
                    "context": "Published 2 hrs ago",
                }
            ]

    class Page:
        url = "https://www.bbc.com/news"

        def locator(self, _selector):
            return Locator()

    source = load_config().source_by_id("bbc_world")
    items = collect_browser_index(Page(), source, "2026-07-14T06:00:00+08:00")

    assert items[0].published_at == "2026-07-14T04:00:00+08:00"


def test_http_5xx_is_failed_not_no_items():
    assert classify_source_status(503, False, False) == SourceStatus.FAILED


def test_challenge_status_takes_precedence():
    assert classify_source_status(403, True, False) == SourceStatus.VERIFICATION_REQUIRED


def test_rate_limit_status_stops_retrying_before_generic_verification():
    assert classify_source_status(429, True, False, True) == SourceStatus.RATE_LIMITED
