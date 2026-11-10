"""Pure business-domain packages for Endury.

Code under ``endury.domain`` should stay deterministic and infrastructure
agnostic. It holds shared models and analysis logic that can be reused by the
HTTP layer, repositories, and planner without pulling in transport concerns.
"""
