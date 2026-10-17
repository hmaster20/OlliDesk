"""Миграция конфига v2 -> v3 одним проходом, с backup config.yaml.v2.bak.

Ключевые маппинги и ИСПРАВЛЕНИЯ дефектов v2:
  slm.selected_model            -> models.analyst.id
  slm.target_n_ctx=32768        -> ПЕРЕОПРЕДЕЛЯЕТСЯ: для CPU-analyst жёсткий cap 8192
                                   (D1: RAM-объём != оправдание большого контекста на CPU)
  slm.n_gpu_layers / auto_gpu   -> models.*.device/n_gpu_layers по арифметике VRAM
  backends.enabled/priority     -> backend_priority
  tool_policies                 -> tools.*
  index_extensions (без .cs/.rb/.tf) -> project_intelligence.index_extensions (расширен)
  agent_max_iterations/chat_max_iterations -> убраны; вместо них agent.default_flow=linear
                                   + deep_mode.threshold_files
  web_search policy             -> security.network.web_search (дефолт neutral + hygiene)
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from core.config_v3 import ConfigV3, ModelRoleCfg
from agents.gpu_utils import detect_optimal_threads  # существующий хелпер


def _usable_vram_mb() -> int:
    try:
        from agents.hardware_profile import get_hardware_profile
        hp = get_hardware_profile()
        return int(getattr(hp.gpu, "usable_vram_mb", 0) or 0)
    except Exception:
        return 0


def migrate(v2: dict[str, Any]) -> ConfigV3:
    slm = v2.get("slm", {}) or {}
    vram = _usable_vram_mb()
    # Арифметика размещения (см. model_pool): 3B влезает в VRAM при kv q8_0@8192.
    planner_device = "GPU" if vram >= 2900 else "CPU"
    analyst_device = "CPU"   # 7B никогда не влезает в T1200-class VRAM
    analyst_ctx = 8192        # ЖЁСТКИЙ cap для CPU (фикс D1), игнорируем v2 32768
    planner_ctx = 8192 if planner_device == "GPU" else 4096

    models = {
        "planner": ModelRoleCfg(id=_pick(v2, planner_default="qwen2.5-3b-instruct"),
                                device=planner_device, n_ctx=planner_ctx,
                                kv_cache="q8_0",
                                n_gpu_layers=(-1 if planner_device == "GPU" else 0),
                                temperature=0.0,
                                budgets={"classify": 32, "plan": 256, "critique": 192}),
        "analyst": ModelRoleCfg(id=slm.get("selected_model") or "qwen2.5-7b-instruct",
                                device=analyst_device, n_ctx=analyst_ctx,
                                kv_cache="q8_0", n_gpu_layers=0, temperature=0.35,
                                budgets={"synthesis": 1100, "regen": 900}),
    }
    net = v2.get("network", {}) or {}
    web = "off" if v2.get("confidential") else str(net.get("web_search", v2.get("tool_policies", {}).get("web_search", "neutral")))
    if web in ("auto",):
        web = "neutral"
    security = {
        "mode": "local_only", "telemetry": False,
        "confidential": bool(v2.get("confidential", False)),
        "network": {"web_search": web, "lsp_download": True,
                    "lsp_download_size_mb_warn": 50, "huggingface_download": True},
    }
    pi = v2.get("project_intelligence", {}) or {}
    idx_ext = list(pi.get("index_extensions") or v2.get("index_extensions") or [])
    # досчитываем недостающие домены (D6)
    for extra in (".cs", ".csproj", ".sln", ".rb", ".tf", ".tfvars", ".hcl", ".pkr.hcl", ".yml"):
        if extra not in idx_ext:
            idx_ext.append(extra)
    cfg = ConfigV3(
        security=__import__("core.config_v3", fromlist=["SecurityCfg"]).SecurityCfg(**security),
        backend_priority=v2.get("backends", {}).get("priority") or v2.get("backend_priority") or ["LOCAL_SLM"],
        embed_backend=v2.get("embed_backend", "auto"),
        models=__import__("core.config_v3", fromlist=["ModelsCfg"]).ModelsCfg(**models),
        context=__import__("core.config_v3", fromlist=["ContextCfg"]).ContextCfg(),
        agent=__import__("core.config_v3", fromlist=["AgentCfg"]).AgentCfg(),
        project_intelligence=__import__("core.config_v3", fromlist=["ProjectIntelligenceCfg"]).ProjectIntelligenceCfg(
            index_extensions=idx_ext,
            index_filenames=__import__("core.config_v3", fromlist=["ProjectIntelligenceCfg"]).ProjectIntelligenceCfg().index_filenames,
            cache_dir=pi.get("cache_dir", ".ollidesk/graph"),
        ),
        tools=__import__("core.config_v3", fromlist=["ToolsPolicyCfg"]).ToolsPolicyCfg(**(v2.get("tool_policies") or {})),
        ignored_patterns=v2.get("ignored_patterns", []),
        last_model=v2.get("last_model"), last_mode=v2.get("last_mode"),
        project_path=v2.get("project_path"),
    )
    return cfg


def _pick(v2: dict, *, planner_default: str) -> str:
    # planner берём из реестра, если есть 3B; иначе дефолт.
    reg = v2.get("model_registry", []) or []
    for m in reg:
        mid = (m.get("id") or "").lower()
        if "3b" in mid and "instruct" in mid:
            return m["id"]
    return planner_default


def migrate_file(path: Path) -> Path:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if int(raw.get("version", 2)) >= 3:
        return path
    backup = path.with_suffix(path.suffix + ".v2.bak")
    shutil.copy2(path, backup)
    cfg = migrate(raw)
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return backup