"""Профиль железа для автооптимизации SLM (кэшируется на процесс)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache

from loguru import logger


@dataclass(frozen=True)
class CPUInfo:
    model: str = ""
    physical_cores: int = 0
    logical_cores: int = 0
    has_avx2: bool = False
    has_avx512: bool = False
    has_fma: bool = False

    @property
    def threads_for_llm(self) -> int:
        """Физические ядра, но не больше 16: HT даёт убывающую отдачу."""
        base = self.physical_cores or self.logical_cores or 4
        return max(2, min(base, 16))


@dataclass(frozen=True)
class GPUInfo:
    index: int
    name: str
    total_vram_mb: int
    free_vram_mb: int
    compute_capability: str
    driver_version: str

    @property
    def usable_vram_mb(self) -> int:
        """Свободная VRAM минус резерв под compute-буферы и драйвер.

        Резерв ~20% от общей (для 4 ГБ ~800 МБ) — эмпирика: меньше приводит
        к OOM при offload, больше — теряем слои.
        """
        reserve = max(400, min(1500, self.total_vram_mb // 5))
        return max(0, self.free_vram_mb - reserve)


@dataclass(frozen=True)
class RAMInfo:
    total_mb: int = 0
    available_mb: int = 0


@dataclass(frozen=True)
class HardwareProfile:
    cpu: CPUInfo = field(default_factory=CPUInfo)
    gpus: tuple[GPUInfo, ...] = ()
    ram: RAMInfo = field(default_factory=RAMInfo)
    os_name: str = ""

    @property
    def best_gpu(self) -> GPUInfo | None:
        cuda = [g for g in self.gpus if g.total_vram_mb > 0]
        return max(cuda, key=lambda g: g.total_vram_mb) if cuda else None

    @property
    def has_cuda(self) -> bool:
        return self.best_gpu is not None

    def describe(self) -> str:
        gpu = self.best_gpu
        gpu_str = (
            f"{gpu.name} ({gpu.total_vram_mb} МБ, свободно {gpu.free_vram_mb})"
            if gpu else "нет CUDA"
        )
        return (
            f"CPU: {self.cpu.model} "
            f"({self.cpu.physical_cores}C/{self.cpu.logical_cores}T, "
            f"AVX2={'да' if self.cpu.has_avx2 else 'нет'}); "
            f"GPU: {gpu_str}; RAM: {self.ram.total_mb // 1024} ГБ"
        )


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, creationflags=flags)
        return r.stdout if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _detect_cpu() -> CPUInfo:
    import platform
    model = platform.processor() or "unknown"
    if sys.platform == "win32":
        out = _run(["wmic", "cpu", "get", "name"])
        if out:
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            if len(lines) >= 2:
                model = lines[1]

    physical = logical = 0
    try:
        import psutil
        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
    except Exception:
        logical = os.cpu_count() or 0
        physical = logical

    low = model.lower()
    has_avx2 = any(k in low for k in ("i3-", "i5-", "i7-", "i9-", "ryzen"))
    has_avx512 = any(k in low for k in ("i9-1", "i7-11", "i9-11", "i9-12")) and "h" not in low

    return CPUInfo(
        model=model.strip(),
        physical_cores=physical,
        logical_cores=logical,
        has_avx2=has_avx2,
        has_avx512=has_avx512,
        has_fma=has_avx2,
    )


def _detect_gpus() -> tuple[GPUInfo, ...]:
    if shutil.which("nvidia-smi") is None:
        return ()
    out = _run([
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.free,compute_cap,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return ()
    gpus: list[GPUInfo] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            gpus.append(GPUInfo(
                index=int(parts[0]),
                name=parts[1],
                total_vram_mb=int(parts[2]),
                free_vram_mb=int(parts[3]),
                compute_capability=parts[4],
                driver_version=parts[5],
            ))
        except ValueError:
            continue
    return tuple(gpus)


def _detect_ram() -> RAMInfo:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return RAMInfo(
            total_mb=vm.total // (1024 * 1024),
            available_mb=vm.available // (1024 * 1024),
        )
    except Exception:
        return RAMInfo()


@lru_cache(maxsize=1)
def get_hardware_profile() -> HardwareProfile:
    """Единожды за процесс собирает профиль железа."""
    profile = HardwareProfile(
        cpu=_detect_cpu(),
        gpus=_detect_gpus(),
        ram=_detect_ram(),
        os_name=sys.platform,
    )
    logger.info(f"Профиль железа: {profile.describe()}")
    return profile