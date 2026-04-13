import sys
from pathlib import Path


def pytest_configure() -> None:
    """
    Make `import bot.*` work when running pytest from repo root.
    """
    effable_root = Path(__file__).resolve().parents[1]  # .../code/EffableProject
    sys.path.insert(0, str(effable_root))

