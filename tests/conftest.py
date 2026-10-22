from __future__ import annotations

import pytest

from app.domain.rules import ClinicRules
from tests.helpers import make_rules


@pytest.fixture
def rules() -> ClinicRules:
    return make_rules()
