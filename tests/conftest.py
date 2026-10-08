"""Shared test setup."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make the repository root importable regardless of where pytest is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests must never reach the network. Fixture mode guarantees that.
os.environ.setdefault("DEMO_MODE", "fixtures")


@pytest.fixture(autouse=True)
def clean_settings():
    """Rebuild settings for each test so env patching takes effect."""
    from app.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


def at(iso: str) -> datetime:
    """Build a UTC datetime from a short ISO string."""
    value = datetime.fromisoformat(iso)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
