"""Policy and runtime settings.

``Policy`` comes from ``config/policy.yaml``. Its fingerprint is stored with every approval,
so changing any rule invalidates earlier approvals. ``Settings`` comes from the environment
and never contains secrets in this part.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .money import CURRENCY_EXPONENTS

PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # the solution/ directory
DEFAULT_POLICY_PATH = PACKAGE_ROOT / "config" / "policy.yaml"

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalPolicy(_Model):
    scrutiny_threshold: Decimal = Field(gt=0, allow_inf_nan=False)
    scrutiny_currency: Currency


class PaymentPolicy(_Model):
    auto_pay_currencies: list[Currency] = Field(min_length=1)
    assume_currency_when_missing: Currency | None = None

    @field_validator("auto_pay_currencies")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        unknown = [c for c in value if c not in CURRENCY_EXPONENTS]
        if unknown:
            raise ValueError(f"currencies without a minor-unit definition: {unknown}")
        return value


class ArithmeticPolicy(_Model):
    tolerance_minor_units: int = Field(ge=0, le=100)


class DatePolicy(_Model):
    slash_date_order: Literal["mdy", "dmy"]
    terms_tolerance_days: int = Field(ge=0, le=30)


class SignalPolicy(_Model):
    review_on_vendor_identity_change: bool
    vendor_identity_change: list[str]
    payment_pressure: list[str]
    payment_instruction: list[str]
    embedded_instructions: list[str]

    @field_validator(
        "vendor_identity_change",
        "payment_pressure",
        "payment_instruction",
        "embedded_instructions",
    )
    @classmethod
    def _compile(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            re.compile(pattern)  # fail at load time, not mid-run
        return patterns


class LimitsPolicy(_Model):
    max_file_bytes: int = Field(gt=0)
    max_text_chars: int = Field(gt=0)
    max_csv_rows: int = Field(gt=0)
    max_pdf_pages: int = Field(gt=0)
    max_line_items: int = Field(gt=0)
    allowed_extensions: list[str]


class Policy(_Model):
    policy_version: str
    approval: ApprovalPolicy
    payment: PaymentPolicy
    arithmetic: ArithmeticPolicy
    dates: DatePolicy
    signals: SignalPolicy
    limits: LimitsPolicy

    @property
    def fingerprint(self) -> str:
        """Hash of every rule value. Changes whenever any policy value changes."""
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def load_policy(path: str | Path | None = None) -> Policy:
    resolved = Path(path or os.environ.get("POLICY_PATH") or DEFAULT_POLICY_PATH)
    with resolved.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return Policy.model_validate(raw)


class Settings(_Model):
    app_env: str = "local"
    data_dir: Path = Path("data/runtime")
    policy_path: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "invoice_flow.db"

    @classmethod
    def from_env(cls) -> Settings:
        policy_path = os.environ.get("POLICY_PATH")
        return cls(
            app_env=os.environ.get("APP_ENV", "local"),
            data_dir=Path(os.environ.get("DATA_DIR", "data/runtime")),
            policy_path=Path(policy_path) if policy_path else None,
        )
