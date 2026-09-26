"""Server settings, read from NIIMBOT_* environment variables and a .env file."""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing_extensions import Annotated

from .imaging import MODELS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NIIMBOT_", env_file=".env", extra="ignore"
    )

    # Printer
    model: str = "b21"
    conn: Literal["usb", "ble", "bluetooth"] = "usb"
    addr: str | None = None
    density: int = 3

    # Defaults applied when a request does not specify them
    label_width_mm: float | None = None
    label_height_mm: float | None = None
    dither: bool = True

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    api_key: str | None = None
    # Browser origins allowed to call the API, e.g. "https://myapp.com,http://localhost:5173".
    # localhost/127.0.0.1 on any port are always allowed. "*" allows every site.
    cors_origins: Annotated[list[str], NoDecode] = []
    allow_image_url: bool = True  # enable image_url on /v1/print/url
    max_upload_mb: int = 20

    # Keep the printer connection open this many seconds after a job (0 = reconnect
    # per job). Saves the ~3 s BLE connect, but blocks the phone app while open.
    keepalive_seconds: float = 60
    job_history: int = 200

    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        v = v.lower()
        if v not in MODELS:
            raise ValueError(f"unknown model '{v}', expected one of {', '.join(MODELS)}")
        return v

    @field_validator("density")
    @classmethod
    def _density(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("density must be between 1 and 5")
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _origins(cls, v):
        if isinstance(v, str):
            v = v.split(",")
        return [o.strip().rstrip("/") for o in v if o and o.strip()]

    @field_validator("addr", "api_key", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return v or None
