from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class AppSettings:
    name: str
    root_dir: Path
    data_dir: Path
    log_dir: Path
    database_path: Path


@dataclass(frozen=True, slots=True)
class LLMSettings:
    provider: str
    base_url: str
    mapping_model: str
    reasoning_model: str
    timeout_seconds: int
    confidence_threshold: float
    api_key: str


@dataclass(frozen=True, slots=True)
class BalancingSettings:
    default_missing_efficiency: float
    solver_time_limit_seconds: int
    minimum_balance_rate: float


@dataclass(frozen=True, slots=True)
class LayoutSettings:
    station_width: int
    station_height: int
    grid_gap: int
    default_flow_volume: float


@dataclass(frozen=True, slots=True)
class TemplateSettings:
    combination_component: str


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppSettings
    llm: LLMSettings
    balancing: BalancingSettings
    layout: LayoutSettings
    templates: TemplateSettings


def _section(config: dict, name: str) -> dict:
    value = config.get(name, {})
    return value if isinstance(value, dict) else {}


def load_settings(config_path: str | Path = "config.toml") -> Settings:
    load_dotenv()
    root_dir = Path.cwd()
    path = Path(config_path)
    config = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    app_cfg = _section(config, "app")
    llm_cfg = _section(config, "llm")
    balancing_cfg = _section(config, "balancing")
    layout_cfg = _section(config, "layout")
    template_cfg = _section(config, "templates")

    data_dir = root_dir / str(app_cfg.get("data_dir", "runtime"))
    log_dir = root_dir / str(app_cfg.get("log_dir", "logs"))

    # Settings stay as plain dataclasses so the rest of the codebase can remain dependency-light and explicit.
    return Settings(
        app=AppSettings(
            name=str(app_cfg.get("name", "车缝生产线车位排产智能体")),
            root_dir=root_dir,
            data_dir=data_dir,
            log_dir=log_dir,
            database_path=data_dir / "factory_agent.sqlite3",
        ),
        llm=LLMSettings(
            provider=str(llm_cfg.get("provider", "deepseek")),
            base_url=str(llm_cfg.get("base_url", "https://api.deepseek.com")),
            mapping_model=str(llm_cfg.get("mapping_model", "deepseek-chat")),
            reasoning_model=str(llm_cfg.get("reasoning_model", "deepseek-reasoner")),
            timeout_seconds=int(llm_cfg.get("timeout_seconds", 60)),
            confidence_threshold=float(llm_cfg.get("confidence_threshold", 0.8)),
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        ),
        balancing=BalancingSettings(
            default_missing_efficiency=float(balancing_cfg.get("default_missing_efficiency", 0.3)),
            solver_time_limit_seconds=int(balancing_cfg.get("solver_time_limit_seconds", 8)),
            minimum_balance_rate=float(balancing_cfg.get("minimum_balance_rate", 0.85)),
        ),
        layout=LayoutSettings(
            station_width=int(layout_cfg.get("station_width", 150)),
            station_height=int(layout_cfg.get("station_height", 88)),
            grid_gap=int(layout_cfg.get("grid_gap", 42)),
            default_flow_volume=float(layout_cfg.get("default_flow_volume", 1.0)),
        ),
        templates=TemplateSettings(
            combination_component=str(template_cfg.get("combination_component", "组合")),
        ),
    )
