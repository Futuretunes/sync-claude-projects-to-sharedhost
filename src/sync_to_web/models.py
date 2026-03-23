from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


DEFAULT_IGNORE_PATTERNS = [
    ".git/*",
    ".venv/*",
    "__pycache__/*",
    "*.pyc",
    ".DS_Store",
]


class TransferProtocol(StrEnum):
    FTP = "ftp"
    FTPS = "ftps"
    SFTP = "sftp"


@dataclass(slots=True)
class ProjectConfig:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "New Project"
    local_path: str = ""
    remote_path: str = "/"
    protocol: TransferProtocol = TransferProtocol.FTP
    host: str = ""
    port: int = 21
    username: str = ""
    credential_key: str = ""
    ignore_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    delete_remote: bool = False
    auto_sync: bool = True
    enabled: bool = True
    claude_sync: bool = False
    build_command: str = ""
    build_output_path: str = ""
    watch_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["protocol"] = self.protocol.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectConfig":
        protocol = TransferProtocol(data.get("protocol", TransferProtocol.FTP.value))
        return cls(
            id=str(data.get("id") or uuid4()),
            name=str(data.get("name") or "New Project"),
            local_path=str(data.get("local_path") or ""),
            remote_path=str(data.get("remote_path") or "/"),
            protocol=protocol,
            host=str(data.get("host") or ""),
            port=int(data.get("port") or default_port_for_protocol(protocol)),
            username=str(data.get("username") or ""),
            credential_key=str(data.get("credential_key") or ""),
            ignore_patterns=list(data.get("ignore_patterns") or DEFAULT_IGNORE_PATTERNS),
            delete_remote=bool(data.get("delete_remote", False)),
            auto_sync=bool(data.get("auto_sync", True)),
            enabled=bool(data.get("enabled", True)),
            claude_sync=bool(data.get("claude_sync", False)),
            build_command=str(data.get("build_command") or ""),
            build_output_path=str(data.get("build_output_path") or ""),
            watch_paths=list(data.get("watch_paths") or []),
        )


def default_port_for_protocol(protocol: TransferProtocol) -> int:
    if protocol == TransferProtocol.SFTP:
        return 22
    return 21
