from __future__ import annotations

import posixpath
import socket
from ftplib import FTP, FTP_TLS, error_perm
from pathlib import Path
from typing import Protocol

import paramiko

from sync_to_web.models import ProjectConfig, TransferProtocol


class RemoteClient(Protocol):
    def connect(self) -> None: ...

    def close(self) -> None: ...

    def ensure_directory(self, remote_directory: str) -> None: ...

    def upload_file(self, local_file: Path, remote_file: str) -> None: ...

    def delete_file(self, remote_file: str) -> None: ...


def build_remote_client(project: ProjectConfig, password: str) -> RemoteClient:
    if project.protocol == TransferProtocol.SFTP:
        return SftpRemoteClient(project, password)
    return FtpRemoteClient(project, password)


def normalize_remote_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip() or "/"
    normalized = posixpath.normpath(cleaned)
    if cleaned.startswith("/") and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


class FtpRemoteClient:
    def __init__(self, project: ProjectConfig, password: str) -> None:
        self.project = project
        self.password = password
        self.client: FTP | FTP_TLS | None = None

    def connect(self) -> None:
        ftp_class: type[FTP] | type[FTP_TLS]
        ftp_class = FTP_TLS if self.project.protocol == TransferProtocol.FTPS else FTP
        client = ftp_class()
        client.connect(self.project.host, self.project.port, timeout=15)
        client.login(self.project.username, self.password)
        if isinstance(client, FTP_TLS):
            client.prot_p()
        self.client = client

    def close(self) -> None:
        if not self.client:
            return
        try:
            self.client.quit()
        except (OSError, EOFError):
            self.client.close()
        self.client = None

    def ensure_directory(self, remote_directory: str) -> None:
        client = self._require_client()
        directory = normalize_remote_path(remote_directory)
        if directory in ("", "/"):
            return

        parts = [part for part in directory.split("/") if part]
        current = "/"
        for part in parts:
            current = posixpath.join(current, part)
            try:
                client.mkd(current)
            except error_perm as exc:
                message = str(exc)
                if not message.startswith("550"):
                    raise

    def upload_file(self, local_file: Path, remote_file: str) -> None:
        client = self._require_client()
        remote_file = normalize_remote_path(remote_file)
        self.ensure_directory(posixpath.dirname(remote_file))
        with local_file.open("rb") as stream:
            client.storbinary(f"STOR {remote_file}", stream)

    def delete_file(self, remote_file: str) -> None:
        client = self._require_client()
        try:
            client.delete(normalize_remote_path(remote_file))
        except error_perm as exc:
            if not str(exc).startswith("550"):
                raise

    def _require_client(self) -> FTP | FTP_TLS:
        if not self.client:
            raise RuntimeError("FTP client is not connected.")
        return self.client


_SFTP_KNOWN_HOSTS = Path.home() / ".sync-to-web" / "known_hosts"


class SftpRemoteClient:
    def __init__(self, project: ProjectConfig, password: str) -> None:
        self.project = project
        self.password = password
        self._ssh: paramiko.SSHClient | None = None
        self.client: paramiko.SFTPClient | None = None

    def connect(self) -> None:
        ssh = paramiko.SSHClient()
        ssh.load_system_host_keys()
        if _SFTP_KNOWN_HOSTS.exists():
            ssh.load_host_keys(str(_SFTP_KNOWN_HOSTS))
        # Trust-on-first-use: unknown hosts are accepted and saved; on subsequent
        # connects the stored key is verified, catching MITM changes.
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            self.project.host,
            port=self.project.port,
            username=self.project.username,
            password=self.password,
            timeout=15,
        )
        _SFTP_KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
        ssh.save_host_keys(str(_SFTP_KNOWN_HOSTS))
        self._ssh = ssh
        self.client = ssh.open_sftp()

    def close(self) -> None:
        if self.client:
            self.client.close()
        if self._ssh:
            self._ssh.close()
        self.client = None
        self._ssh = None

    def ensure_directory(self, remote_directory: str) -> None:
        client = self._require_client()
        directory = normalize_remote_path(remote_directory)
        if directory in ("", "/"):
            return

        parts = [part for part in directory.split("/") if part]
        current = "/"
        for part in parts:
            current = posixpath.join(current, part)
            try:
                client.stat(current)
            except FileNotFoundError:
                client.mkdir(current)

    def upload_file(self, local_file: Path, remote_file: str) -> None:
        client = self._require_client()
        remote_file = normalize_remote_path(remote_file)
        self.ensure_directory(posixpath.dirname(remote_file))
        client.put(str(local_file), remote_file)

    def delete_file(self, remote_file: str) -> None:
        client = self._require_client()
        try:
            client.remove(normalize_remote_path(remote_file))
        except FileNotFoundError:
            return

    def _require_client(self) -> paramiko.SFTPClient:
        if not self.client:
            raise RuntimeError("SFTP client is not connected.")
        return self.client


def can_resolve_host(host: str) -> bool:
    try:
        socket.gethostbyname(host)
        return True
    except OSError:
        return False
