import logging
import sys


def setup_logging(level=logging.INFO):
    """Единая настройка логирования для всего проекта."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.setLevel(level)
    root.addHandler(handler)
