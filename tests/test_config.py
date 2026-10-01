from pathlib import Path

from leadqual.config import load_settings
from leadqual.models import SizeBand, SpendBand, Workload

CONFIG = Path(__file__).parent.parent / "config.toml"


def test_shipped_config_is_valid():
    settings = load_settings(CONFIG)

    assert len(settings.competitors) == 3
    assert {place.level for place in settings.sanctions} == {"blocked", "review"}


def test_every_known_band_has_points():
    scoring = load_settings(CONFIG).scoring

    assert set(scoring.size_points) == set(SizeBand) - {SizeBand.UNKNOWN}
    assert set(scoring.spend_points) == set(SpendBand) - {SpendBand.UNKNOWN}
    assert set(scoring.workload_points) == set(Workload) - {Workload.UNKNOWN}


def test_thresholds_are_ordered():
    scoring = load_settings(CONFIG).scoring

    assert 0 < scoring.medium_priority_min < scoring.high_priority_min <= 100
