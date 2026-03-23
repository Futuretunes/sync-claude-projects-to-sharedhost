from __future__ import annotations

import json
from pathlib import Path

import keyring

from sync_to_web.models import ProjectConfig


APP_DIR = Path.home() / ".sync-to-web"
CONFIG_PATH = APP_DIR / "projects.json"
KEYRING_SERVICE = "sync-to-web"


class ConfigStore:
    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def load_projects(self) -> list[ProjectConfig]:
        if not self.config_path.exists():
            return []

        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        return [ProjectConfig.from_dict(item) for item in payload.get("projects", [])]

    def save_projects(self, projects: list[ProjectConfig]) -> None:
        payload = {
            "version": 1,
            "projects": [project.to_dict() for project in projects],
        }
        self.config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def save_password(self, project: ProjectConfig, password: str) -> None:
        key = self._credential_key(project)
        keyring.set_password(KEYRING_SERVICE, key, password)

    def load_password(self, project: ProjectConfig) -> str:
        key = self._credential_key(project)
        return keyring.get_password(KEYRING_SERVICE, key) or ""

    def delete_password(self, project: ProjectConfig) -> None:
        key = self._credential_key(project)
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass

    @staticmethod
    def _credential_key(project: ProjectConfig) -> str:
        return project.credential_key or f"project:{project.id}"
