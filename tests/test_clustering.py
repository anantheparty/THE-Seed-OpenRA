from __future__ import annotations

from dataclasses import dataclass

from openra_api.models import Location
from openra_state.intel.clustering import SpatialClustering


@dataclass
class DummyUnit:
    name: str
    position: Location | None


def test_cluster_units_dbscan_reuses_point_clustering_with_duplicate_positions():
    units = [
        DummyUnit("u1", Location(0, 0)),
        DummyUnit("u2", Location(1, 1)),
        DummyUnit("u3", Location(0, 0)),
        DummyUnit("u4", Location(50, 50)),
        DummyUnit("u5", Location(52, 51)),
        DummyUnit("u6", None),
    ]

    clusters = SpatialClustering.cluster_units_dbscan(units, eps=3.0, min_samples=1)
    cluster_names = sorted(sorted(unit.name for unit in cluster) for cluster in clusters)

    assert cluster_names == [["u1", "u2", "u3"], ["u4", "u5"]]
