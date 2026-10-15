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
            f"GPU: {gpu_str}; RAM: {self.ram.total_mb // 1024} ГБ "
            f"(доступно {self.ram.available_mb // 1024} ГБ)"
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
    """Определяет модель CPU и число ядер.

    На Windows используем `wmic cpu get Name,NumberOfCores,
    NumberOfLogicalProcessors /format:csv` — он даёт и имя, и оба счётчика
    ядер. Раньше полагались на `psutil.cpu_count(logical=False)`, и на
    i7-11800H он возвращал 16 вместо 8 (то есть logical вместо physical).
    """
    import platform

    model = platform.processor() or "unknown"
    physical = 0
    logical = 0

    # Windows: wmic даёт всё сразу.
    if sys.platform == "win32":
        out = _run(
            [
                "wmic", "cpu", "get",
                "Name,NumberOfCores,NumberOfLogicalProcessors",
                "/format:csv",
            ]
        )
        if out:
            lines = [ln for ln in out.splitlines() if ln.strip()]
            # Первая строка — заголовок, вторая — данные:
            # Node,Name,NumberOfCores,NumberOfLogicalProcessors
            for line in lines[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    try:
                        name = parts[1]
                        cores = int(parts[2])
                        threads = int(parts[3])
                    except (ValueError, IndexError):
                        continue
                    if name:
                        model = name
                    if cores > 0:
                        physical = cores
                    if threads > 0:
                        logical = threads
                    break

    # Fallback: psutil.
    if not physical or not logical:
        try:
            import psutil
            physical = physical or (psutil.cpu_count(logical=False) or 0)
            logical = logical or (psutil.cpu_count(logical=True) or 0)
        except Exception:
            pass

    if not logical:
        logical = os.cpu_count() or 4
    if not physical:
        # Если не смогли определить физические — считаем, что HT нет.
        physical = logical

    # Защита от завышенного physical == logical при включённом HT.
    # Если физических столько же, сколько логических, а модель похожа на
    # современный Intel с HT — делим пополам.
    if physical == logical and physical > 4:
        low = model.lower()
        if any(k in low for k in ("i3-", "i5-", "i7-", "i9-", "ryzen")):
            physical = max(2, physical // 2)

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


def _detect_ram_via_psutil() -> RAMInfo | None:
    """Основной путь: psutil."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        total_mb = vm.total // (1024 * 1024)
        avail_mb = vm.available // (1024 * 1024)
        if total_mb > 0:
            return RAMInfo(total_mb=total_mb, available_mb=avail_mb)
    except Exception as e:
        logger.debug(f"psutil RAM detection failed: {e}")
    return None


def _detect_ram_via_winapi() -> RAMInfo | None:
    """Windows: GlobalMemoryStatusEx через ctypes."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return RAMInfo(
                total_mb=stat.ullTotalPhys // (1024 * 1024),
                available_mb=stat.ullAvailPhys // (1024 * 1024),
            )
    except Exception as e:
        logger.debug(f"GlobalMemoryStatusEx failed: {e}")
    return None


def _detect_ram_via_proc() -> RAMInfo | None:
    """Linux: /proc/meminfo."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        data: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    data[parts[0].strip()] = int(parts[1].strip().split()[0])
        total_kb = data.get("MemTotal", 0)
        avail_kb = data.get("MemAvailable", data.get("MemFree", 0))
        if total_kb > 0:
            return RAMInfo(
                total_mb=total_kb // 1024,
                available_mb=avail_kb // 1024,
            )
    except Exception as e:
        logger.debug(f"/proc/meminfo failed: {e}")
    return None


def _detect_ram() -> RAMInfo:
    """Определяет RAM с несколькими fallback-стратегиями.

    Порядок: psutil → Windows API (ctypes) → /proc/meminfo (Linux).

    Раньше эта функция полагалась только на psutil, и на некоторых системах
    он возвращал 0 (пустой RAMInfo). Оптимизатор SLM считал, что RAM нет, и
    прирезал n_ctx до минимума (1024 токена) даже при 128 ГБ физической памяти.
    """
    for detector, name in (
        (_detect_ram_via_psutil, "psutil"),
        (_detect_ram_via_winapi, "WinAPI"),
        (_detect_ram_via_proc, "/proc/meminfo"),
    ):
        info = detector()
        if info is not None and info.total_mb > 0:
            logger.debug(
                f"RAM: {info.total_mb // 1024} ГБ "
                f"(доступно {info.available_mb // 1024} ГБ) via {name}"
            )
            return info

    logger.warning("Не удалось определить объём RAM ни одним способом")
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