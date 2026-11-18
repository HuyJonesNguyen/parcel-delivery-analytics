"""Shared settings and input loading for the MIP steps (26 to 32).

Every modelling assumption that more than one script needs lives here,
so a value is typed once and every step reads the same number.

This file is imported by the other MIP scripts. It does nothing when
run on its own.

All figures are synthetic modelling assumptions. They are NOT
Australia Post routes, prices, fleet data or operating records.
"""

from collections import namedtuple
from math import ceil, isfinite
from pathlib import Path

import duckdb


# ============================================================
# PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Historical synthetic parcels. Always opened read-only.
SOURCE_DB_PATH = PROJECT_ROOT / "data" / "processed" / "parcel_delivery.duckdb"

# Everything the MIP steps create.
MIP_DB_PATH = PROJECT_ROOT / "data" / "processed" / "mip_network.duckdb"


# ============================================================
# SETTINGS (change a value here and rerun Steps 30, 31 and 32)
# ============================================================

# Scenarios that Steps 31 and 32 solve. Each must exist in the
# mip_scenarios table (created by mip_facility_registry.py).
SCENARIOS_TO_RUN = ("BASE_2027", "PEAK_2027")

# Straight-line distance x this factor = modelled road distance.
# Illustrative only. It is not a verified road-distance factor.
DISTANCE_MULTIPLIER = 1.20

# Days between linehaul departures. 7 means every lane's parcels
# are collected for a week and then leave together.
# A smaller number means faster service and emptier vehicles.
# Tested values: 7 (weekly), 2 and 1 (daily).
PERIOD_DAYS = 7

# Parcels never pack perfectly. A vehicle is treated as full when
# this share of its usable volume is taken.
MAX_VOLUME_FILL = 0.85

# A lane may transfer through one other facility only if the trip
# through that facility is at most this many times the direct
# distance. 1.50 allows a 50 percent detour and blocks routes such
# as Sydney -> Perth -> Melbourne.
MAX_DETOUR_RATIO = 1.50

# Parcel size is taken from the parcel's recorded weight_kg.
# Each pair is (size name, upper weight limit in kg).
# None means "no upper limit". The names must match the rows in
# mip_parcel_profiles, which supply volume and handling cost.
SIZE_WEIGHT_BANDS_KG = (
    ("Small", 1.0),
    ("Medium", 3.0),
    ("Large", 10.0),
    ("Oversized", None),
)

# ---- Step 33: candidate facility choice ------------------------

# Total parcels per year to plan for. They are spread over states,
# lanes and weeks in the same proportions as the historical data.
# With the current registry Sydney goes over capacity above about
# 706,000 parcels and Melbourne above about 833,000.
N_PARCELS = 900_000

# A facility's yearly workload = parcels lodged in its state plus,
# if this is True, interstate parcels arriving in its state.
COUNT_ARRIVALS_IN_WORKLOAD = True

# Yearly fixed cost for a candidate whose registry cost is still 0,
# in AUD per parcel of yearly capacity. None means "use the same
# rate as the Active facilities" (their total fixed cost divided by
# their total capacity, about A$0.83 with the current registry).
CANDIDATE_COST_PER_CAPACITY_AUD = None

# Step 33 prices trucks by solving the Step 32 model for a sample
# of periods and scaling up. 4 = every fourth period (13 of 52).
# 1 = every period: exact, and about four times slower.
TRUCK_COST_EVERY_NTH_PERIOD = 4

# HiGHS settings for Steps 32 and 33.
# A gap of 0 asks for the proven best plan, not "within 0.01%".
SOLVER_TIME_LIMIT_SECONDS = 60
SOLVER_RELATIVE_GAP = 0.0


# ============================================================
# SMALL DATA CONTAINERS
# ============================================================

# One vehicle type from mip_vehicle_types.
Vehicle = namedtuple(
    "Vehicle",
    "volume_m3 payload_kg dispatch_cost_aud cost_per_km_aud"
)

# The freight on one lane in one period.
Load = namedtuple(
    "Load",
    "parcels volume_m3 weight_kg handling_cost_aud"
)


# ============================================================
# COST AND CAPACITY RULES (used by Step 31 and Step 32)
# ============================================================

def trip_cost(vehicle, distance_km):
    """Cost of one one-way trip: dispatch fee plus running cost."""
    return vehicle.dispatch_cost_aud + distance_km * vehicle.cost_per_km_aud


def usable_volume(vehicle):
    """Volume a vehicle can carry after the packing allowance."""
    return vehicle.volume_m3 * MAX_VOLUME_FILL


def trips_needed(volume_m3, weight_kg, vehicle):
    """Fewest trips of ONE vehicle type that carry a load.

    Both limits apply: enough volume and enough payload.
    The tiny 1e-9 stops a load of exactly one vehicle from being
    rounded up to two because of floating-point noise.
    """
    by_volume = ceil(volume_m3 / usable_volume(vehicle) - 1e-9)
    by_weight = ceil(weight_kg / vehicle.payload_kg - 1e-9)
    return max(by_volume, by_weight, 0)


# ============================================================
# SCENARIOS
# ============================================================

def get_scenario(con, scenario_id):
    """Return (planning_year, demand_multiplier) for one scenario."""
    row = con.execute("""
        SELECT planning_year, demand_multiplier
        FROM mip_scenarios
        WHERE scenario_id = ?
    """, [scenario_id]).fetchone()

    if row is None:
        raise ValueError(
            f"Scenario {scenario_id} is not in mip_scenarios. "
            "Run src/mip_facility_registry.py first."
        )

    planning_year, multiplier = row

    if not isfinite(multiplier) or multiplier <= 0:
        raise ValueError(f"Invalid demand multiplier for {scenario_id}.")

    return int(planning_year), float(multiplier)


# ============================================================
# INPUT LOADING FOR STEPS 31 AND 32
# ============================================================

def load_linehaul_inputs(scenario_id):
    """Read and validate everything a linehaul model needs.

    Returns a dictionary with:
        planning_year, demand_multiplier
        states      sorted list of states that have an Active facility
        facility    {state: facility_id}
        vehicles    {vehicle_type_id: Vehicle}
        lane_km     {(origin_state, destination_state): modelled km}
        demand      {period: {(origin_state, destination_state): Load}}
                    interstate lanes only
        interstate_parcels, intrastate_parcels
    """

    if not MIP_DB_PATH.is_file():
        raise FileNotFoundError(
            f"MIP database not found: {MIP_DB_PATH}. "
            "Run src/mip_facility_registry.py first."
        )

    with duckdb.connect(str(MIP_DB_PATH), read_only=True) as con:

        planning_year, multiplier = get_scenario(con, scenario_id)

        active = con.execute("""
            SELECT state, facility_id
            FROM facility_registry
            WHERE status = 'Active'
            ORDER BY state, facility_id
        """).fetchall()

        vehicle_rows = con.execute("""
            SELECT vehicle_type_id, usable_volume_m3, max_payload_kg,
                   fixed_dispatch_cost_aud, operating_cost_per_km_aud
            FROM mip_vehicle_types
            ORDER BY vehicle_type_id
        """).fetchall()

        distance_rows = con.execute("""
            SELECT origin_facility_id, destination_facility_id, distance_km
            FROM mip_facility_distances
            WHERE distance_method = 'Haversine'
        """).fetchall()

        # Add the four parcel sizes together: the linehaul models
        # only need the total load per lane and period.
        demand_rows = con.execute("""
            SELECT period, origin_state, destination_state,
                   SUM(parcel_count), SUM(total_volume_m3),
                   SUM(total_weight_kg), SUM(total_handling_cost_aud)
            FROM mip_demand_by_period
            WHERE scenario_id = ?
            GROUP BY period, origin_state, destination_state
            ORDER BY period, origin_state, destination_state
        """, [scenario_id]).fetchall()

    if not active or not vehicle_rows or not distance_rows:
        raise ValueError(
            "Facilities, vehicle types or distances are missing. "
            "Run Steps 1 to 29 first."
        )

    if not demand_rows:
        raise ValueError(
            f"No demand stored for {scenario_id}. "
            "Run src/mip_demand.py first."
        )

    # One Active facility per state: the linehaul runs between them.
    facility = {}
    for state, facility_id in active:
        if state in facility:
            raise ValueError(
                f"More than one Active facility in {state}. "
                "These steps need exactly one per state."
            )
        facility[state] = facility_id

    vehicles = {}
    for vehicle_id, volume, payload, dispatch, per_km in vehicle_rows:
        values = (volume, payload, dispatch, per_km)
        if any(v is None or not isfinite(float(v)) for v in values):
            raise ValueError(f"Invalid vehicle parameters: {vehicle_id}")
        if volume <= 0 or payload <= 0 or dispatch < 0 or per_km < 0:
            raise ValueError(f"Invalid vehicle capacity or cost: {vehicle_id}")
        vehicles[vehicle_id] = Vehicle(
            float(volume), float(payload), float(dispatch), float(per_km)
        )

    facility_km = {}
    for origin_id, destination_id, km in distance_rows:
        if km is None or not isfinite(float(km)) or km < 0:
            raise ValueError(f"Invalid distance: {origin_id} -> {destination_id}")
        facility_km[(origin_id, destination_id)] = float(km)

    states = sorted(facility)

    lane_km = {}
    for origin in states:
        for destination in states:
            if origin == destination:
                continue
            pair = (facility[origin], facility[destination])
            if pair not in facility_km:
                raise ValueError(
                    f"Distance missing for {pair}. "
                    "Run src/mip_transport_costs.py first."
                )
            lane_km[(origin, destination)] = (
                facility_km[pair] * DISTANCE_MULTIPLIER
            )

    demand = {}
    interstate_parcels = 0
    intrastate_parcels = 0

    for period, origin, destination, count, volume, weight, handling in demand_rows:

        values = (count, volume, weight, handling)
        if any(v is None or not isfinite(float(v)) or v < 0 for v in values):
            raise ValueError(
                f"Invalid demand: period {period}, {origin} -> {destination}"
            )

        if origin not in facility or destination not in facility:
            raise ValueError(
                f"No Active facility for {origin} -> {destination}."
            )

        # Intrastate parcels never ride the interstate linehaul.
        if origin == destination:
            intrastate_parcels += int(count)
            continue

        interstate_parcels += int(count)

        demand.setdefault(int(period), {})[(origin, destination)] = Load(
            int(count), float(volume), float(weight), float(handling)
        )

    if not demand:
        raise ValueError("No interstate demand found.")

    return {
        "scenario_id": scenario_id,
        "planning_year": planning_year,
        "demand_multiplier": multiplier,
        "states": states,
        "facility": facility,
        "vehicles": vehicles,
        "lane_km": lane_km,
        "demand": demand,
        "interstate_parcels": interstate_parcels,
        "intrastate_parcels": intrastate_parcels,
    }