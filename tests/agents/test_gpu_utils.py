import os
from unittest.mock import MagicMock, patch

from agents.gpu_utils import detect_optimal_threads


class TestDetectOptimalThreads:
    def test_uses_physical_cores(self):
        """На гиперпоточном CPU берём физические ядра, а не логические."""
        with patch("psutil.cpu_count", side_effect=lambda logical=True: 8 if not logical else 16):
            assert detect_optimal_threads() == 8

    def test_falls_back_to_os_cpu_count(self):
        psutil_stub = MagicMock()
        psutil_stub.cpu_count.side_effect = RuntimeError("нет psutil")

        with patch.dict("sys.modules", {"psutil": psutil_stub}), patch(
            "os.cpu_count", return_value=12
        ):
            assert detect_optimal_threads() == 12

    def test_minimum_is_two(self):
        psutil_stub = MagicMock()
        psutil_stub.cpu_count.side_effect = RuntimeError("нет psutil")

        with patch.dict("sys.modules", {"psutil": psutil_stub}), patch(
            "os.cpu_count", return_value=1
        ):
            assert detect_optimal_threads() == 2

    def test_single_core_machine(self):
        with patch("psutil.cpu_count", side_effect=lambda logical=True: 4 if not logical else 4):
            assert detect_optimal_threads() == 4

    def test_no_cpu_info_at_all(self):
        psutil_stub = MagicMock()
        psutil_stub.cpu_count.side_effect = RuntimeError("нет psutil")

        with patch.dict("sys.modules", {"psutil": psutil_stub}), patch(
            "os.cpu_count", return_value=None
        ):
            assert detect_optimal_threads() == 2

    def test_never_exceeds_logical_count(self):
        physical = 8
        with patch("psutil.cpu_count", side_effect=lambda logical=True: 16 if logical else physical):
            assert detect_optimal_threads() <= (os.cpu_count() or 2)