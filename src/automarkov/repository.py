"""向后兼容的重导出镜像——新代码请直接导入 adapters.repository 子包。"""

from automarkov.adapters.repository import (  # noqa: F401
    ArtifactParentContractError,
    ArtifactSchemaRegistry,
    InMemoryArtifactRepository,
    ParentBinding,
    SqliteArtifactRepository,
    _default_artifact_id,
    _default_schema_registry,
)
