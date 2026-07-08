from __future__ import annotations

import oci
from oci.auth.signers import get_resource_principals_signer
from oci_openai import OciResourcePrincipalAuth, OciSessionAuth, OciUserPrincipalAuth

from config import (
    OCI_AUTH_MODE,
    OCI_CONFIG_PROFILE,
    OCI_CONFIG_FILE,
    REGION,
)


def build_oci_auth(
    *,
    mode: str | None = None,
    profile_name: str | None = None,
    config_file: str | None = None,
):
    auth_mode = (mode or OCI_AUTH_MODE).strip().lower()
    selected_profile = (profile_name or OCI_CONFIG_PROFILE).strip() or OCI_CONFIG_PROFILE
    selected_config = OCI_CONFIG_FILE if config_file is None else config_file.strip()

    auth_kwargs = {"profile_name": selected_profile}
    if selected_config:
        auth_kwargs["config_file"] = selected_config

    if auth_mode == "session":
        return OciSessionAuth(**auth_kwargs)
    if auth_mode == "user":
        return OciUserPrincipalAuth(**auth_kwargs)
    if auth_mode == "resource_principal":
        return OciResourcePrincipalAuth()
    raise ValueError(
        f"Unsupported OCI auth mode={auth_mode!r}. Use 'session', 'user', or 'resource_principal'."
    )


def build_oci_sdk_client_kwargs(
    *,
    mode: str | None = None,
    profile_name: str | None = None,
    config_file: str | None = None,
) -> dict:
    auth_mode = (mode or OCI_AUTH_MODE).strip().lower()
    selected_profile = (profile_name or OCI_CONFIG_PROFILE).strip() or OCI_CONFIG_PROFILE
    selected_config = OCI_CONFIG_FILE if config_file is None else config_file.strip()

    if auth_mode in {"user", "session"}:
        config_kwargs = {"profile_name": selected_profile}
        if selected_config:
            config_kwargs["file_location"] = selected_config
        config = oci.config.from_file(**config_kwargs)
        return {"config": config}

    if auth_mode == "resource_principal":
        return {
            "config": {"region": REGION},
            "signer": get_resource_principals_signer(),
        }

    raise ValueError(
        f"Unsupported OCI auth mode={auth_mode!r}. Use 'session', 'user', or 'resource_principal'."
    )
