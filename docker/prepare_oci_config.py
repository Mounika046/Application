from __future__ import annotations

import os
from pathlib import Path


def _normalize_mounted_oci_path(raw_value: str, mount_dir: Path) -> str:
    value = (raw_value or "").strip()
    if not value:
        return value

    cleaned = value.replace("\\\\", "\\")
    marker = ".oci\\"
    idx = cleaned.lower().find(marker.lower())
    if idx >= 0:
        suffix = cleaned[idx + len(marker):].replace("\\", "/")
        return str(mount_dir / suffix)

    filename = Path(cleaned.replace("\\", "/")).name
    if filename:
        candidate = mount_dir / filename
        if candidate.exists():
            return str(candidate)
    return value


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default)).strip()


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return default


def _decode_env_multiline(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return value
    return value.replace("\\n", "\n")


def _write_generated_user_principal_config(*, target_config: Path, mount_dir: Path) -> bool:
    auth_mode = _first_env("OCI_EAI_AUTH_MODE", "OCI_AUTH_MODE", default="").lower()
    if auth_mode != "user":
        return False

    tenancy = _first_env("OCI_EAI_USER_TENANCY_OCID", "OCI_USER_TENANCY_OCID")
    user = _first_env("OCI_EAI_USER_OCID", "OCI_USER_OCID")
    fingerprint = _first_env("OCI_EAI_USER_FINGERPRINT", "OCI_USER_FINGERPRINT")
    region = _first_env("OCI_REGION", "OCI_EAI_USER_REGION", "OCI_USER_REGION")
    private_key = _decode_env_multiline(
        _first_env("OCI_EAI_USER_PRIVATE_KEY_PEM", "OCI_USER_PRIVATE_KEY_PEM")
    )
    pass_phrase = _decode_env_multiline(
        _first_env("OCI_EAI_USER_PRIVATE_KEY_PASSPHRASE", "OCI_USER_PRIVATE_KEY_PASSPHRASE")
    )
    profile = _first_env("OCI_EAI_CONFIG_PROFILE", "OCI_CONFIG_PROFILE", default="DEFAULT") or "DEFAULT"

    required = {
        "OCI_EAI_USER_TENANCY_OCID": tenancy,
        "OCI_EAI_USER_OCID": user,
        "OCI_EAI_USER_FINGERPRINT": fingerprint,
        "OCI_REGION": region,
        "OCI_EAI_USER_PRIVATE_KEY_PEM": private_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "User principal auth is selected, but required OCI user auth env vars are missing: "
            + ", ".join(missing)
        )

    target_config.parent.mkdir(parents=True, exist_ok=True)
    generated_dir = target_config.parent
    generated_dir.mkdir(parents=True, exist_ok=True)

    key_path = generated_dir / "user_api_key.pem"
    key_path.write_text(private_key.rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    config_lines = [
        f"[{profile}]",
        f"user={user}",
        f"fingerprint={fingerprint}",
        f"tenancy={tenancy}",
        f"region={region}",
        f"key_file={key_path}",
    ]
    if pass_phrase:
        config_lines.append(f"pass_phrase={json_escape_for_oci_config(pass_phrase)}")

    target_config.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    return True


def json_escape_for_oci_config(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def main() -> None:
    source_config = Path(os.environ.get("OCI_SOURCE_CONFIG_FILE", "/run/oci/config"))
    target_config = Path(os.environ.get("OCI_TARGET_CONFIG_FILE", "/tmp/oci/config.generated"))
    mount_dir = Path(os.environ.get("OCI_MOUNT_DIR", "/run/oci"))

    if _write_generated_user_principal_config(
        target_config=target_config,
        mount_dir=mount_dir,
    ):
        return

    if not source_config.exists():
        return

    target_config.parent.mkdir(parents=True, exist_ok=True)

    rewritten_lines = []
    for raw_line in source_config.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if "=" not in stripped or stripped.startswith("[") or stripped.startswith("#") or stripped.startswith(";"):
            rewritten_lines.append(raw_line)
            continue

        key, value = raw_line.split("=", 1)
        normalized_key = key.strip().lower()
        if normalized_key in {"key_file", "security_token_file"}:
            value = _normalize_mounted_oci_path(value, mount_dir)
            rewritten_lines.append(f"{key.strip()}={value}")
        else:
            rewritten_lines.append(raw_line)

    target_config.write_text("\n".join(rewritten_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
