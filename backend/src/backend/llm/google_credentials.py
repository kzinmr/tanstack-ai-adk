"""
Google credential helpers for Vertex AI (AWS-friendly).
"""

from __future__ import annotations

import json
import os
import tempfile

from google.oauth2 import service_account

# Module-level reference to keep temp file alive for process lifetime
_temp_credentials_file: tempfile.NamedTemporaryFile | None = None


def setup_google_credentials() -> tuple[service_account.Credentials, str]:
    """Resolve service account credentials from env (JSON string or file path).

    Supports both:
    - File path: /path/to/service-account.json
    - JSON string: {"type":"service_account","project_id":"...","private_key":"..."}

    For JSON strings, writes to a temp file since Google Auth libraries expect a file path.
    Also sets GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION for ADK/google-genai.
    """
    global _temp_credentials_file

    service_account_value = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not service_account_value:
        raise ValueError(
            "GOOGLE_APPLICATION_CREDENTIALS environment variable not found."
        )

    credentials_file_path: str | None = None

    if os.path.exists(service_account_value):
        # It's a valid file path - use it directly
        credentials_file_path = service_account_value
        with open(service_account_value) as handle:
            service_account_info = json.load(handle)
    elif service_account_value.strip().startswith("{"):
        # It's a JSON string - parse and write to temp file
        try:
            service_account_info = json.loads(service_account_value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "GOOGLE_APPLICATION_CREDENTIALS is not a valid JSON string."
            ) from exc
        # Write to temp file (kept open for process lifetime)
        _temp_credentials_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(service_account_info, _temp_credentials_file, ensure_ascii=False)
        _temp_credentials_file.flush()
        credentials_file_path = _temp_credentials_file.name
        # Update env var to point to the temp file
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file_path
    elif service_account_value.endswith(".json"):
        raise FileNotFoundError(
            f"GOOGLE_APPLICATION_CREDENTIALS file not found: {service_account_value}"
        )
    else:
        raise ValueError(
            "GOOGLE_APPLICATION_CREDENTIALS must be a file path or JSON string."
        )

    project = service_account_info.get("project_id")
    if not project:
        raise ValueError("project_id not found in service account info.")

    # Set project and location for ADK/google-genai Vertex AI backend
    if "GOOGLE_CLOUD_PROJECT" not in os.environ:
        os.environ["GOOGLE_CLOUD_PROJECT"] = project
    region = os.environ.get("GCP_REGION") or os.environ.get("GOOGLE_CLOUD_LOCATION")
    if region and "GOOGLE_CLOUD_LOCATION" not in os.environ:
        os.environ["GOOGLE_CLOUD_LOCATION"] = region
    # Enable Vertex AI backend for ADK
    if "GOOGLE_GENAI_USE_VERTEXAI" not in os.environ:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return credentials, project
