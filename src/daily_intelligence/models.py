from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SourceStatus(StrEnum):
    """处理：定义来源状态的可用枚举值。
    输入：
    - 无显式业务参数：不声明额外构造字段；该定义以 ``StrEnum`` 为基础，
      通过类成员承担“定义来源状态的可用枚举值”职责。
    输出：构造后的 ``SourceStatus`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    SUCCESS = "success"
    PARTIAL = "partial"
    VERIFICATION_REQUIRED = "verification_required"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"
    NO_ITEMS = "no_items"


class ContentStatus(StrEnum):
    """处理：定义正文状态的可用枚举值。
    输入：
    - 无显式业务参数：不声明额外构造字段；该定义以 ``StrEnum`` 为基础，
      通过类成员承担“定义正文状态的可用枚举值”职责。
    输出：构造后的 ``ContentStatus`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    NOT_FETCHED = "not_fetched"
    FULL_TEXT = "full_text"
    PARTIAL = "partial"
    METADATA_ONLY = "metadata_only"
    VERIFICATION_REQUIRED = "verification_required"
    FAILED = "failed"


@dataclass(slots=True)
class ArticleItem:
    """处理：封装文章条目相关的数据与行为。
    输入：
    - ``item_id``：规范条目的稳定 ID；用于连接索引、正文、简报和图片。
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``source_name``：供读者展示的来源名称；与稳定 source_id 一起写入条目。
    - ``title``：来源提供的标题文本；会清理空白，并用于过滤、身份或展示。
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    - ``canonical_url``：已去跟踪参数并规范主机和路径的 URL；参与稳定身份计算。
    - ``discovered_at``：本轮发现时间的 ISO 字符串；在缺少发布时间时作为可追溯时间。
    - ``module``：报告顶层领域 ID，例如 information 或 technology。
    - ``category``：报告栏目 ID；必须与 module 和当前 taxonomy 契约一致。
    - ``content_status``：正文采集状态；区分全文、部分、元数据、失败和待验证。
    - ``description``：来源提供的摘要或正文片段；只作为不可信文本保存和展示。
    - ``published_at``：来源声明的发布时间；缺失时保持为空，不用采集时间冒充。
    - ``original_provider``：聚合来源记录的原始内容提供方名称。
    - ``image_url``：来源提供的首选图片 URL；下载前仍需执行公网和格式校验。
    - ``content_path``：已保存正文 Markdown 的路径；只有位于当前数据根时才可复用。
    - ``content_characters``：成功提取正文的字符数，用于区分全文和部分正文。
    - ``metadata``：不进入核心字段的来源、排名、图片候选和采集方式元数据。
    输出：构造后的 ``ArticleItem`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    item_id: str
    source_id: str
    source_name: str
    title: str
    url: str
    canonical_url: str
    discovered_at: str
    module: str = "information"
    category: str = "international"
    content_status: str = ContentStatus.NOT_FETCHED
    description: str = ""
    published_at: str | None = None
    original_provider: str | None = None
    image_url: str | None = None
    content_path: str | None = None
    content_characters: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """处理：将当前对象转换为可序列化字典。
        输入：
        - 无显式业务参数：不接收额外业务参数；
          从当前实例读取“将当前对象转换为可序列化字典”所需状态。
        输出：“将当前对象转换为可序列化字典”形成的结构化字典；
          键值表达该处理定义的业务记录或查找关系。
        """
        return asdict(self)


@dataclass(slots=True)
class SourceResult:
    """处理：封装来源结果相关的数据与行为。
    输入：
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``source_name``：供读者展示的来源名称；与稳定 source_id 一起写入条目。
    - ``source_url``：来源或图片的原始 URL；进入网络或索引前会执行相应规范化与安全检查。
    - ``status``：当前操作或来源状态；值必须属于对应的显式状态模型。
    - ``collected_at``：页面或 Feed 的采集时间；用于状态记录和时间回退，不冒充发布时间。
    - ``module``：报告顶层领域 ID，例如 information 或 technology。
    - ``category``：报告栏目 ID；必须与 module 和当前 taxonomy 契约一致。
    - ``page_title``：采集时观察到的页面标题；用于挑战诊断和审计。
    - ``final_url``：导航或重定向完成后的页面 URL。
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    - ``error``：上游异常或错误信息；用于保留失败语义。
    - ``challenge``：结构化访问挑战详情，例如限流、验证码文本或 iframe 检测。
    - ``page_results``：多页面来源的逐页状态、错误和条目摘要。
    - ``items``：规范条目列表；每项带稳定身份并可进入聚类、报告或渲染步骤。
    输出：构造后的 ``SourceResult`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    source_id: str
    source_name: str
    source_url: str
    status: str
    collected_at: str
    module: str = "information"
    category: str = "international"
    page_title: str = ""
    final_url: str = ""
    http_status: int | None = None
    error: str | None = None
    challenge: dict[str, Any] = field(default_factory=dict)
    page_results: list[dict[str, Any]] = field(default_factory=list)
    items: list[ArticleItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """处理：将当前对象转换为可序列化字典。
        输入：
        - 无显式业务参数：不接收额外业务参数；
          从当前实例读取“将当前对象转换为可序列化字典”所需状态。
        输出：“将当前对象转换为可序列化字典”形成的结构化字典；
          键值表达该处理定义的业务记录或查找关系。
        """
        data = asdict(self)
        data["items_count"] = len(self.items)
        return data
