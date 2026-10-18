"""B4 喘振风险代理与安全边界子包。

对外主要入口:
  - surge.assess_surge_risk(snapshot, candidate, *, boundary_version) -> SurgeAssessment
  - boundaries.build_boundaries() / boundary_flow()
"""
from air_station.safety import boundaries, surge  # noqa: F401

__all__ = ["boundaries", "surge"]
