def setup_logger(log_dir: Path, level: str = "INFO") -> Logger:
    """Настраивает loguru с ротацией 10MB, хранение 7 дней."""
