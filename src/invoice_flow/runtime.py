"""Runtime wiring: configuration from the environment, provider selection, event recording.

Provider selection rules (these are safety rules, not conveniences):
* ``mock`` always means the labeled simulated reader. It is only used when asked for.
* ``live`` needs a server-side API key AND a numeric spending cap. Without both, a live run is
  refused with a clear error. It never falls back to mock or to another model.
* the model name and base URL come from the operator's environment only, never from a request.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .budget import RunBudget
from .config import Policy, Settings, load_policy
from .providers import OpenRouterProvider, Provider, ProviderError, redact
from .providers.openrouter import DEFAULT_BASE_URL
from .providers.simulated import SimulatedProvider
from .storage import Database, append_event, utc_now

ProviderFactory = Callable[[str], Provider]

DEFAULT_MODEL = "x-ai/grok-4.7"


def _float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None


@dataclass(frozen=True)
class AppConfig:
    """Everything configurable. Secrets live here but are never exposed by ``public()``."""

    app_env: str = "local"
    data_dir: Path = Path("data/runtime")
    storage_persistence: Literal["local", "persistent", "ephemeral"] = "ephemeral"
    default_mode: str = "mock"
    openrouter_api_key: str | None = None
    llm_model: str = DEFAULT_MODEL
    llm_base_url: str = DEFAULT_BASE_URL
    model_call_limit: int = 8
    tool_call_limit: int = 6
    run_timeout_seconds: float = 90.0
    request_timeout_seconds: float = 30.0
    max_output_tokens: int = 1500
    model_budget_usd: float | None = None
    demo_access_code: str | None = None
    session_secret: str | None = None
    queue_capacity: int = 20
    max_concurrent_runs: int = 1
    rate_limit_per_minute: int = 30
    session_ttl_seconds: int = 8 * 3600
    allow_live_in_sessions: bool = False

    @classmethod
    def from_env(cls) -> AppConfig:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip() or None
        app_env = os.environ.get("APP_ENV", "local")
        persistence = os.environ.get("STORAGE_PERSISTENCE", "").strip() or (
            "ephemeral" if app_env == "production" else "local"
        )
        if persistence not in ("local", "persistent", "ephemeral"):
            raise ValueError("STORAGE_PERSISTENCE must be local, persistent or ephemeral")
        return cls(
            app_env=app_env,
            data_dir=Path(os.environ.get("DATA_DIR", "data/runtime")),
            storage_persistence=persistence,
            default_mode=os.environ.get("MODEL_MODE", "mock").strip() or "mock",
            openrouter_api_key=key,
            llm_model=os.environ.get("LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            llm_base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).strip()
            or DEFAULT_BASE_URL,
            model_call_limit=_int("MODEL_CALL_LIMIT", 8),
            tool_call_limit=_int("TOOL_CALL_LIMIT", 6),
            run_timeout_seconds=_float("RUN_TIMEOUT_SECONDS", 90.0) or 90.0,
            request_timeout_seconds=_float("REQUEST_TIMEOUT_SECONDS", 30.0) or 30.0,
            max_output_tokens=_int("MAX_OUTPUT_TOKENS", 1500),
            model_budget_usd=_float("MODEL_BUDGET_USD", None),
            demo_access_code=os.environ.get("DEMO_ACCESS_CODE", "").strip() or None,
            session_secret=os.environ.get("SESSION_SECRET", "").strip() or None,
            queue_capacity=_int("QUEUE_CAPACITY", 20),
            max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 1),
            rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 30),
            session_ttl_seconds=_int("SESSION_TTL_SECONDS", 8 * 3600),
            allow_live_in_sessions=os.environ.get("ALLOW_LIVE_IN_SESSIONS", "") == "1",
        )

    @property
    def settings(self) -> Settings:
        return Settings(app_env=self.app_env, data_dir=self.data_dir)

    def live_ready(self) -> tuple[bool, str]:
        """Is a live run permitted? A key and a positive numeric cap are both required."""
        if not self.openrouter_api_key:
            return False, "OPENROUTER_API_KEY is not configured."
        if self.model_budget_usd is None or self.model_budget_usd <= 0:
            return False, "MODEL_BUDGET_USD (a positive spending cap) is not configured."
        return True, ""

    def modes_enabled(self, *, for_session: bool = False) -> list[str]:
        modes = ["mock"]
        ready, _ = self.live_ready()
        if ready and (not for_session or self.allow_live_in_sessions):
            modes.append("live")
        return modes

    def new_budget(self) -> RunBudget:
        return RunBudget(
            max_requests=self.model_call_limit,
            max_tool_calls=self.tool_call_limit,
            timeout_seconds=self.run_timeout_seconds,
        )

    def public(self) -> dict[str, Any]:
        """Non-secret values only. Safe to send to a browser."""
        return {
            "model_call_limit": self.model_call_limit,
            "run_timeout_seconds": self.run_timeout_seconds,
            "queue_capacity": self.queue_capacity,
            "default_mode": self.default_mode,
        }


def default_provider_factory(config: AppConfig) -> ProviderFactory:
    def build(mode: str) -> Provider:
        if mode == "mock":
            return SimulatedProvider()
        if mode == "live":
            ready, why = config.live_ready()
            if not ready:
                raise ProviderError("live_not_configured", f"Live mode is unavailable: {why}")
            return OpenRouterProvider(
                config.openrouter_api_key or "", config.llm_model, base_url=config.llm_base_url
            )
        raise ProviderError("unknown_mode", f"Unknown mode '{str(mode)[:20]}'.")

    return build


@dataclass
class App:
    """The shared application context used by the CLI and the API alike."""

    config: AppConfig
    db: Database
    policy: Policy
    provider_factory: ProviderFactory
    samples_dir: Path | None = None
    upload_dir: Path | None = None
    # the simulated bank call; tests may replace it. ``None`` uses the brief's mock_payment.
    mock_payment: Callable[..., Any] | None = None
    _extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        config: AppConfig | None = None,
        *,
        provider_factory: ProviderFactory | None = None,
        db: Database | None = None,
        policy: Policy | None = None,
        samples_dir: Path | None = None,
    ) -> App:
        cfg = config or AppConfig.from_env()
        database = db or Database(cfg.settings.db_path)
        from .storage import init_database

        init_database(database)
        return cls(
            config=cfg,
            db=database,
            policy=policy or load_policy(),
            provider_factory=provider_factory or default_provider_factory(cfg),
            samples_dir=samples_dir,
        )

    def live_spend_usd(self) -> float:
        """Known cost of completed live runs. Runs whose cost was unknown add nothing here;
        that gap is stated in the docs, not hidden."""
        import json

        total = 0.0
        with self.db.read() as conn:
            for row in conn.execute(
                "SELECT usage_json FROM runs WHERE mode = 'live' AND usage_json IS NOT NULL"
            ):
                try:
                    cost = json.loads(row["usage_json"]).get("estimated_cost_usd")
                except ValueError:
                    continue
                if cost is not None:
                    total += float(cost)
        return total

    def check_live_allowed(self) -> None:
        ready, why = self.config.live_ready()
        if not ready:
            raise ProviderError("live_not_configured", f"Live mode is unavailable: {why}")
        cap = self.config.model_budget_usd or 0.0
        if self.live_spend_usd() >= cap:
            raise ProviderError(
                "spend_cap_reached", f"The configured spending cap (${cap:g}) has been reached."
            )


class EventRecorder:
    """Persists every event the moment it happens, each in its own short transaction.

    A crash mid-run therefore still leaves the events that already occurred. Payloads are
    clipped and scrubbed of anything that looks like a key before they are stored.
    """

    def __init__(self, db: Database, run_id: str, secrets: tuple[str | None, ...] = ()) -> None:
        self._db = db
        self.run_id = run_id
        self._secrets = secrets

    def __call__(self, stage: str, type_: str, payload: dict[str, Any] | None = None) -> None:
        clean = self._scrub(payload or {})
        with self._db.transaction() as conn:
            append_event(conn, self.run_id, stage, type_, clean, self._db.now())

    def _scrub(self, value: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "..."
        if isinstance(value, str):
            return redact(value, *self._secrets)[:2000]
        if isinstance(value, dict):
            return {str(k)[:60]: self._scrub(v, depth + 1) for k, v in list(value.items())[:40]}
        if isinstance(value, (list, tuple)):
            return [self._scrub(v, depth + 1) for v in list(value)[:40]]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[:200]


def now_iso() -> str:
    return utc_now().isoformat()
