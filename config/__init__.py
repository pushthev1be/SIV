"""Settings from environment variables, falling back to the outputs `azd provision` saved locally.

No secrets: every Azure call uses Entra ID. Locally that is the `az login` session; in Azure it is
the container apps' managed identity (set AZURE_CLIENT_ID).
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@cache
def _azd_values() -> dict[str, str]:
    azure = ROOT / ".azure"
    env_name = os.environ.get("AZURE_ENV_NAME")
    if not env_name and (azure / "config.json").exists():
        import json

        env_name = json.loads((azure / "config.json").read_text(encoding="utf-8")).get("defaultEnvironment")
    path = azure / (env_name or "") / ".env"
    if not env_name or not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"')
    return values


def setting(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or _azd_values().get(name) or default
    if value is None:
        raise RuntimeError(f"{name} is not set; run `azd provision` or export it")
    return value


def credential():
    """Async Entra ID credential: managed identity in Azure, the Azure CLI session locally."""
    from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

    client_id = os.environ.get("AZURE_CLIENT_ID")
    return ManagedIdentityCredential(client_id=client_id) if client_id else AzureCliCredential()
