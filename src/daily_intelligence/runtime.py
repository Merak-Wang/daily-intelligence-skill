from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import now_iso, read_json, write_json

DATA_ROOT_REGISTRY_SCHEMA = "1.0"


def _is_relative_to(path: Path, root: Path) -> bool:
    """处理：判断规范路径是否位于指定根目录之内。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``root``：安全边界或检查根目录；目标路径必须位于其中。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def require_data_root_path(path: Path, data_dir: Path, label: str) -> Path:
    """处理：拒绝不属于当前数据根的控制或数据 artifact。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``label``：用于错误消息的字段或产物名称，使失败能定位到具体输入。
    输出：指向“拒绝不属于当前数据根的控制或数据 artifact”所生成、定位或确认产物的本地路径。
    """
    resolved = path.expanduser().resolve()
    root = data_dir.expanduser().resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(
            f"{label} is outside the active DAILY_INTEL_DATA_DIR: {resolved}. "
            f"Active root: {root}. Re-run with --data-dir {resolved_data_root_hint(resolved)} "
            "or adopt one canonical root before continuing."
        )
    return resolved


def resolved_data_root_hint(path: Path) -> str:
    """处理：从已知 artifact 目录推断数据根，以生成可操作错误。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    输出：“从已知 artifact 目录推断数据根，以生成可操作错误”得到的规范字符串，
      供调用方存储、比较或展示。
    """
    artifact_directories = {
        "runs",
        "indexes",
        "context",
        "content",
        "reports",
        "evaluations",
        "state",
        "publishing",
        "locks",
        "challenges",
    }
    for parent in (path, *path.parents):
        if parent.name in artifact_directories:
            return f'"{parent.parent}"'
    return f'"{path.parent}"'


def validate_run_data_root(run: dict[str, Any], run_path: Path, data_dir: Path) -> Path:
    """处理：校验运行数据根目录并在不满足约束时报告错误。
    输入：
    - ``run``：当前运行清单对象；包含状态、尝试次数、截止时间和产物路径。
    - ``run_path``：运行清单 JSON 路径；记录当前阶段、产物血缘、截止时间和恢复动作。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“校验运行数据根目录并在不满足约束时报告错误”所生成、定位或确认产物的本地路径。
    """
    root = data_dir.expanduser().resolve()
    # 所有清单与其引用的产物必须属于同一数据根，防止跨运行串写状态。
    require_data_root_path(run_path, root, "Run manifest")
    recorded = run.get("data_root")
    if recorded and Path(str(recorded)).expanduser().resolve() != root:
        raise ValueError(
            "Run manifest data_root does not match the active DAILY_INTEL_DATA_DIR: "
            f"run={Path(str(recorded)).expanduser().resolve()}, active={root}"
        )
    artifacts = run.get("artifacts", {})
    if isinstance(artifacts, dict):
        for key in (
            "index_path",
            "context_path",
            "json_path",
            "markdown_path",
            "html_path",
            "pdf_path",
            "local_index_path",
        ):
            value = artifacts.get(key)
            if value:
                require_data_root_path(Path(str(value)), root, f"Run artifact {key}")
    return root


def data_root_registry_path(hermes_home: Path) -> Path:
    """处理：返回 Hermes 保存 SignalTrail 数据根绑定的登记表路径。
    输入：
    - ``hermes_home``：Hermes 实例主目录；其登记表保存唯一 SignalTrail 数据根绑定。
    输出：指向“返回 Hermes 保存 SignalTrail 数据根绑定的登记表路径”所生成、定位或确认产物的本地
      路径。
    """
    return hermes_home.expanduser().resolve() / "state" / "daily-intelligence-data-root.json"


def load_bound_data_root(hermes_home: Path) -> Path | None:
    """处理：读取并校验 Hermes 已绑定的 SignalTrail 数据根。
    输入：
    - ``hermes_home``：Hermes 实例主目录；其登记表保存唯一 SignalTrail 数据根绑定。
    输出：指向“读取并校验 Hermes 已绑定的 SignalTrail 数据根”所生成、定位或确认产物的本地路径；
      条件不满足时返回 None。
    """
    registry_path = data_root_registry_path(hermes_home)
    if not registry_path.exists():
        return None
    payload = read_json(registry_path)
    if not isinstance(payload, dict) or not payload.get("data_root"):
        raise ValueError(f"Invalid daily-intelligence data-root registry: {registry_path}")
    return Path(str(payload["data_root"])).expanduser().resolve()


def bind_data_root(
    data_dir: Path,
    hermes_home: Path,
    *,
    adopt: bool = False,
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """处理：将一个 Hermes 实例绑定到唯一数据根，开发和测试目录不写入全局登记。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``hermes_home``：Hermes 实例主目录；其登记表保存唯一 SignalTrail 数据根绑定。
    - ``adopt``：是否显式接管已有数据根绑定；未授权时冲突会中止。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：“将一个 Hermes 实例绑定到唯一数据根，开发和测试目录不写入全局登记”形成的结构化字典；
      典型键包括 data_root、previous_data_root、registry_path、schema_version、status、updated_a
      t。
    """
    root = data_dir.expanduser().resolve()
    home = hermes_home.expanduser().resolve()
    if not _is_relative_to(root, home):
        # 外部开发/测试目录不污染 Hermes 的全局绑定登记表。
        return {"status": "external_unbound", "data_root": str(root)}

    registry_path = data_root_registry_path(home)
    previous = load_bound_data_root(home)
    if previous and previous != root and not adopt:
        # 更换记录系统必须显式 adopt，避免无意中把一段历史分裂到两个数据根。
        raise ValueError(
            "SignalTrail is already bound to another data root: "
            f"{previous}. Refusing to use {root}. Use `daily-intel --data-dir \"{root}\" "
            "data-root adopt` only after confirming the intended history."
        )
    payload = {
        "schema_version": DATA_ROOT_REGISTRY_SCHEMA,
        "data_root": str(root),
        "updated_at": now_iso(timezone),
        **({"previous_data_root": str(previous)} if previous and previous != root else {}),
    }
    write_json(registry_path, payload)
    return {
        "status": "adopted" if adopt and previous != root else "bound",
        "registry_path": str(registry_path),
        **payload,
    }
