"""
Google credential helpers for Vertex AI (AWS-friendly).
"""

from __future__ import annotations

import json
import os

from google.oauth2 import service_account


def setup_google_credentials() -> tuple[service_account.Credentials, str]:
    """Resolve service account credentials from env (JSON string or file path)."""
    service_account_value = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not service_account_value:
        raise ValueError(
            "GOOGLE_APPLICATION_CREDENTIALS environment variable not found."
        )

    if os.path.exists(service_account_value):
        with open(service_account_value) as handle:
            service_account_info = json.load(handle)
    elif service_account_value.endswith(".json"):
        raise FileNotFoundError(
            "GOOGLE_APPLICATION_CREDENTIALS JSON file does not exist."
        )
    else:
        try:
            service_account_info = json.loads(service_account_value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "GOOGLE_APPLICATION_CREDENTIALS is not a valid JSON string."
            ) from exc

    project = service_account_info.get("project_id")
    if not project:
        raise ValueError("project_id not found in service account info.")

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = json.dumps(
        service_account_info, ensure_ascii=False
    )

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return credentials, project
