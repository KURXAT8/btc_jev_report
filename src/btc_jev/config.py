"""Explicit, side-effect-free settings loading with a mail-only legacy boundary."""

from collections.abc import Mapping
from pathlib import Path
import re
import shlex
from typing import Literal

from pydantic import ValidationError

from btc_jev.domain import Settings


MAIL_KEYS = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL")
SETTING_KEYS = {
    "DATA_DIR": "data_dir", "TIMEZONE": "timezone", "JEV_MODEL": "jev_model",
    "CALIBRATION_MODE": "calibration_mode",
    "TYPESAFE_API_KEY": "typesafe_api_key", "GMAIL_ADDRESS": "gmail_address",
    "BINANCE_API_KEY": "binance_api_key", "BINANCE_API_SECRET": "binance_api_secret",
    "GMAIL_APP_PASSWORD": "gmail_app_password", "RECIPIENT_EMAIL": "recipient_email",
}


class ConfigurationError(ValueError):
    """Safe to show to an operator: contains setting names, never their values."""


def select_mail_settings(values: dict[str, str]) -> dict[str, str]:
    return {key: values[key] for key in MAIL_KEYS if key in values}


def _read_env(path: Path, label: str, *, optional: bool = False) -> dict[str, str]:
    """Parse single-line KEY=value entries; never expand variables or execute text.

    Supports optional export, BOM, comments, single/double quotes. Multiline
    values and shell programs are deliberately not part of this file format.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        if optional:
            return {}
        raise ConfigurationError(f"Cannot read {label}") from None
    except (OSError, UnicodeError, ValueError):
        raise ConfigurationError(f"Cannot read {label}") from None

    values = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if match is None:
            raise ConfigurationError(f"Invalid {label} entry at line {number}")
        key, value = match.groups()
        if value.startswith(("'", '"')):
            try:
                parts = shlex.split(value, comments=True, posix=True)
                if len(parts) != 1:
                    raise ValueError
                value = parts[0]
            except ValueError:
                raise ConfigurationError(f"Invalid {label} quoting at line {number}") from None
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def _local_path(value: str, root: Path, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Invalid {name}")
    try:
        path = Path(value)
        return (root / path).resolve()
    except (OSError, ValueError, RuntimeError):
        raise ConfigurationError(f"Invalid {name}") from None


def load_settings(environ: Mapping[str, str], project_env: Path) -> Settings:
    """Precedence: provided process environment > project file > legacy mail fields.

    No implicit real mail-file access. Set MAIL_ENV_FILE explicitly to opt in;
    a blank override disables it. This function neither creates directories
    nor updates os.environ. An absent project .env permits offline operations.
    """
    root = project_env.parent.resolve()
    local = _read_env(project_env, "project env", optional=True)
    combined = {**local, **environ}
    mail_path = combined.get("MAIL_ENV_FILE", "")
    mail = {}
    if mail_path:
        path = _local_path(mail_path, root, "MAIL_ENV_FILE")
        mail = select_mail_settings(_read_env(path, "MAIL_ENV_FILE"))
    values = {**mail, **combined}
    # Backward-compatible with the names already entered for this deployment.
    if "BINANCE_API_KEY" not in values and "API_KEY" in values:
        values["BINANCE_API_KEY"] = values["API_KEY"]
    if "BINANCE_API_SECRET" not in values and "secret_KEY" in values:
        values["BINANCE_API_SECRET"] = values["secret_KEY"]
    settings = {field: values[key] for key, field in SETTING_KEYS.items() if key in values}
    settings["data_dir"] = _local_path(values.get("DATA_DIR", "data"), root, "DATA_DIR")
    try:
        return Settings(**settings)
    except ValidationError as error:
        names = sorted({key for key, field in SETTING_KEYS.items()
                        if any(item["loc"] and item["loc"][0] == field
                               for item in error.errors(include_input=False, include_context=False))})
        raise ConfigurationError("Invalid configuration: " + ", ".join(names)) from None


def require_secrets(settings: Settings, operation: Literal["jev", "mail", "binance"]) -> None:
    """Call only at the operation boundary; status/preview need no credentials."""
    if operation == "jev":
        keys = ("TYPESAFE_API_KEY",)
    elif operation == "mail":
        keys = MAIL_KEYS
    elif operation == "binance":
        keys = ("BINANCE_API_KEY", "BINANCE_API_SECRET")
    else:
        raise ConfigurationError("Unknown credential operation")
    missing = [key for key in keys if not getattr(settings, SETTING_KEYS[key]).get_secret_value().strip()]
    if missing:
        raise ConfigurationError("Missing required settings: " + ", ".join(missing))
