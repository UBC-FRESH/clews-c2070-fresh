"""Stage A facility assignment for national assortment supply.

The package is deliberately separate from the Stage B network-flow model in
:mod:`fresh_fibre`.  Stage A parses supply, facility, transport, and contract
records into a small transportation LP; Stage B consumes the emitted
``supply_offer``, ``technology``, and ``transport_link`` subset.

The TSA24/BC pilot is diagnostic.  It is not a TSA supply estimate, it does not
claim official BC grade equivalence, and it does not use MKRF.
"""

from __future__ import annotations

from . import units
from .facilities import (
    FacilityError,
    FacilityRecord,
    FacilityTable,
    build_facility_capacities,
    build_facility_table,
)
from .fixtures import FixtureError, Tsa24Fixture, build_tsa24_fixture
from .model import AssignmentSolveError, solve_assignment
from .schema import (
    Assignment,
    AssignmentError,
    AssignmentInputs,
    AssignmentResult,
    Contract,
    FacilityCapacity,
    FacilityOffer,
    FeedstockGrade,
    SupplyOffer,
    TransportLink,
    UnassignedSupply,
    to_stage_b_tables,
)
from .transport import (
    TransportPath,
    TransportTable,
    apply_real_road_links,
    build_transport_table,
)
from .validate import (
    AssignmentValidationError,
    ValidationCheck,
    ValidationReport,
    validate_assignment,
)

__all__ = [
    "Assignment",
    "AssignmentError",
    "AssignmentInputs",
    "AssignmentResult",
    "AssignmentSolveError",
    "AssignmentValidationError",
    "Contract",
    "FacilityCapacity",
    "FacilityError",
    "FacilityOffer",
    "FacilityRecord",
    "FacilityTable",
    "FeedstockGrade",
    "FixtureError",
    "SupplyOffer",
    "TransportLink",
    "TransportPath",
    "TransportTable",
    "Tsa24Fixture",
    "UnassignedSupply",
    "ValidationCheck",
    "ValidationReport",
    "apply_real_road_links",
    "build_facility_capacities",
    "build_facility_table",
    "build_transport_table",
    "build_tsa24_fixture",
    "solve_assignment",
    "to_stage_b_tables",
    "units",
    "validate_assignment",
]
