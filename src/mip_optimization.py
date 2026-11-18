"""
Step 32: Linehaul network plan, a Mixed Integer Linear Program (MILP)
solved with PuLP 4.x and HiGHS.

Run:
    python src/mip_optimization.py

Requirements:
    pip install duckdb pandas pulp highspy

Prerequisites:
    Step 26: Facility distances      (mip_transport_costs.py)
    Step 29: Vehicle types           (mip_vehicle_registry.py)
    Step 30: Demand by period        (mip_demand.py)
    Step 31: Direct-shipping baseline (mip_truck_allocation.py)

THE BUSINESS QUESTION
    Each period, parcels wait at eight state facilities for 56
    interstate lanes. Most lanes carry far less than one vehicle
    load. Sending a separate vehicle down every lane (Step 31)
    moves mostly air.

    Two lanes can share a vehicle if one of them transfers at a
    facility on the way. Darwin -> Hobart parcels can ride the
    Darwin -> Adelaide vehicle, change over in Adelaide, and ride
    Adelaide -> Hobart with Adelaide's own parcels.

    Which lanes should transfer, where, and which vehicles should
    run on which legs, so that total cost is lowest?

WHY THIS NEEDS A SOLVER
    A lane has up to seven options (direct, or via one of six other
    facilities). With 56 lanes that is 7^56 combinations, and the
    best choice for one lane depends on what every other lane does,
    because they share vehicles. A spreadsheet rule cannot search
    that. A MILP can, and it proves the answer is the cheapest.

All costs, capacities and distances are synthetic assumptions.
"""

from collections import defaultdict
from time import perf_counter

import duckdb
import pandas as pd
import pulp

from mip_common import (
    MAX_DETOUR_RATIO,
    MAX_VOLUME_FILL,
    MIP_DB_PATH,
    PERIOD_DAYS,
    SCENARIOS_TO_RUN,
    SOLVER_RELATIVE_GAP,
    SOLVER_TIME_LIMIT_SECONDS,
    load_linehaul_inputs,
    trip_cost,
    trips_needed,
    usable_volume,
)


# ============================================================
# STEP 32.1: ROUTE OPTIONS
# ============================================================

def find_route_options(states, lane_km):
    """List the ways each lane's parcels may travel.

    Returns {lane: [None, hub, hub, ...]}.
        None = direct, origin to destination.
        hub  = origin -> hub -> destination, one transfer.

    A hub is offered only if the trip through it is at most
    MAX_DETOUR_RATIO times the direct distance. At most one
    transfer keeps transit time and handling under control.
    """

    options = {}

    for lane, direct_km in lane_km.items():

        origin, destination = lane
        hubs = []

        for hub in states:
            if hub in lane:
                continue

            via_km = lane_km[(origin, hub)] + lane_km[(hub, destination)]

            if via_km <= MAX_DETOUR_RATIO * direct_km:
                hubs.append(hub)

        options[lane] = [None] + hubs

    return options


# ============================================================
# STEP 32.2: BUILD AND SOLVE ONE PERIOD
# ============================================================

def solve_period(period, loads, route_options, vehicles, lane_km):
    """Find the cheapest routes and vehicle trips for one period.

    Decision variables:
        use_route[lane, hub] = 1 if the lane's parcels take that
                               route (hub None = direct), else 0.
        trips[leg, vehicle]  = whole number of trips of a vehicle
                               type on a leg (origin, destination).

    Objective (minimise):
        cost of every trip
        + handling cost of every parcel that transfers at a hub

    Constraints:
        1. Every lane takes exactly one route.
        2. On every leg, the vehicles sent have enough usable
           volume for all parcels routed over that leg.
        3. The same for payload.

    Returns a dictionary describing the plan for this period.
    """

    model = pulp.LpProblem(f"Linehaul_Network_Period_{period}", pulp.LpMinimize)

    # --------------------------------------------------------
    # 32.2.1 ROUTE VARIABLES, AND WHICH ROUTES USE WHICH LEG
    # --------------------------------------------------------

    use_route = {}
    routes_on_leg = defaultdict(list)

    for lane in sorted(loads):

        origin, destination = lane

        for hub in route_options[lane]:

            name = f"route_{origin}_{destination}_{hub or 'DIRECT'}"

            use_route[(lane, hub)] = model.add_variable(
                name, cat=pulp.LpBinary
            )

            if hub is None:
                legs = [lane]
            else:
                legs = [(origin, hub), (hub, destination)]

            for leg in legs:
                routes_on_leg[leg].append((lane, hub))

        # Constraint 1: exactly one route per lane.
        model += (
            pulp.lpSum(
                use_route[(lane, hub)] for hub in route_options[lane]
            ) == 1,
            f"One_Route_{origin}_{destination}"
        )

    # --------------------------------------------------------
    # 32.2.2 TRIP VARIABLES AND CAPACITY CONSTRAINTS
    # --------------------------------------------------------

    trips = {}

    for leg in sorted(routes_on_leg):

        leg_origin, leg_destination = leg

        for vehicle_id in vehicles:
            trips[(leg, vehicle_id)] = model.add_variable(
                f"trips_{leg_origin}_{leg_destination}_{vehicle_id}",
                lowBound=0,
                cat=pulp.LpInteger
            )

        users = routes_on_leg[leg]

        # Constraint 2: volume.
        model += (
            pulp.lpSum(
                usable_volume(vehicles[vehicle_id])
                * trips[(leg, vehicle_id)]
                for vehicle_id in vehicles
            ) >= pulp.lpSum(
                loads[lane].volume_m3 * use_route[(lane, hub)]
                for lane, hub in users
            ),
            f"Volume_{leg_origin}_{leg_destination}"
        )

        # Constraint 3: payload.
        model += (
            pulp.lpSum(
                vehicles[vehicle_id].payload_kg
                * trips[(leg, vehicle_id)]
                for vehicle_id in vehicles
            ) >= pulp.lpSum(
                loads[lane].weight_kg * use_route[(lane, hub)]
                for lane, hub in users
            ),
            f"Payload_{leg_origin}_{leg_destination}"
        )

    # --------------------------------------------------------
    # 32.2.3 OBJECTIVE
    # --------------------------------------------------------

    # A transferred parcel is unloaded, sorted and reloaded once
    # more, so it pays its handling cost a second time.
    model += (
        pulp.lpSum(
            trip_cost(vehicles[vehicle_id], lane_km[leg])
            * trips[(leg, vehicle_id)]
            for leg, vehicle_id in trips
        )
        + pulp.lpSum(
            loads[lane].handling_cost_aud * use_route[(lane, hub)]
            for lane, hub in use_route
            if hub is not None
        ),
        "Truck_And_Transfer_Cost_AUD"
    )

    # --------------------------------------------------------
    # 32.2.4 SOLVE
    # --------------------------------------------------------

    solver = pulp.HiGHS(
        msg=False,
        timeLimit=SOLVER_TIME_LIMIT_SECONDS,
        gapRel=SOLVER_RELATIVE_GAP
    )

    solve_stats = model.solve(solver)

    if solve_stats.status != pulp.LpSolveStatus.Optimal:
        raise RuntimeError(
            f"Period {period}: optimality not established "
            f"({solve_stats.status_str}). No results were saved. "
            "Raise SOLVER_TIME_LIMIT_SECONDS in mip_common.py."
        )

    # --------------------------------------------------------
    # 32.2.5 READ THE CHOSEN ROUTES
    # --------------------------------------------------------

    chosen_hub = {}

    for (lane, hub), variable in use_route.items():
        if pulp.value(variable) > 0.5:
            if lane in chosen_hub:
                raise AssertionError(f"Two routes chosen for {lane}.")
            chosen_hub[lane] = hub

    if set(chosen_hub) != set(loads):
        raise AssertionError(f"Period {period}: a lane has no route.")

    # Add up what each leg really carries under the chosen routes.
    leg_volume = defaultdict(float)
    leg_weight = defaultdict(float)
    transfer_cost = 0.0

    for lane, hub in chosen_hub.items():

        origin, destination = lane
        load = loads[lane]

        if hub is None:
            legs = [lane]
        else:
            legs = [(origin, hub), (hub, destination)]
            transfer_cost += load.handling_cost_aud

        for leg in legs:
            leg_volume[leg] += load.volume_m3
            leg_weight[leg] += load.weight_kg

    # --------------------------------------------------------
    # 32.2.6 READ THE TRIPS AND CHECK THEM INDEPENDENTLY
    # --------------------------------------------------------

    leg_trips = defaultdict(dict)
    truck_cost = 0.0

    for (leg, vehicle_id), variable in trips.items():

        value = pulp.value(variable)

        if value is None or abs(value - round(value)) > 1e-5:
            raise ValueError(
                f"Period {period}: trip count is not a whole number "
                f"for {leg}, {vehicle_id}."
            )

        trip_count = int(round(value))

        if trip_count > 0:
            leg_trips[leg][vehicle_id] = trip_count
            truck_cost += (
                trip_count * trip_cost(vehicles[vehicle_id], lane_km[leg])
            )

    # Do not trust the solver blindly: recheck every leg.
    for leg in leg_volume:

        sent_volume = sum(
            count * usable_volume(vehicles[vehicle_id])
            for vehicle_id, count in leg_trips[leg].items()
        )
        sent_payload = sum(
            count * vehicles[vehicle_id].payload_kg
            for vehicle_id, count in leg_trips[leg].items()
        )

        if sent_volume + 1e-6 < leg_volume[leg]:
            raise AssertionError(f"Period {period}: volume short on {leg}.")

        if sent_payload + 1e-6 < leg_weight[leg]:
            raise AssertionError(f"Period {period}: payload short on {leg}.")

    plan_cost = truck_cost + transfer_cost

    if abs(plan_cost - pulp.value(model.objective)) > 0.01:
        raise AssertionError(
            f"Period {period}: recalculated cost differs from the solver."
        )

    # Sending every lane direct in its cheapest single vehicle type
    # is one of the plans the solver could have picked, so the
    # optimal plan can never cost more than that.
    direct_cost = sum(
        min(
            trips_needed(load.volume_m3, load.weight_kg, vehicle)
            * trip_cost(vehicle, lane_km[lane])
            for vehicle in vehicles.values()
        )
        for lane, load in loads.items()
    )

    if plan_cost > direct_cost + 0.01:
        raise AssertionError(
            f"Period {period}: plan costs more than direct shipping."
        )

    return {
        "chosen_hub": chosen_hub,
        "leg_trips": dict(leg_trips),
        "leg_volume": dict(leg_volume),
        "truck_cost": truck_cost,
        "transfer_cost": transfer_cost,
        "direct_cost": direct_cost,
    }


# ============================================================
# STEP 32: OPTIMISE THE LINEHAUL NETWORK FOR ONE SCENARIO
# ============================================================

def optimise_linehaul_network(scenario_id):
    """Solve every period of one scenario, save and report the plan."""

    # --------------------------------------------------------
    # 32.3 VALIDATE ENVIRONMENT AND LOAD DATA
    # --------------------------------------------------------

    if int(pulp.__version__.split(".")[0]) < 4:
        raise RuntimeError(
            f"PuLP 4.x required. Installed: {pulp.__version__}"
        )

    if not pulp.HiGHS(msg=False).available():
        raise RuntimeError(
            "HiGHS unavailable. Install using: "
            "python -m pip install highspy"
        )

    inputs = load_linehaul_inputs(scenario_id)

    vehicles = inputs["vehicles"]
    lane_km = inputs["lane_km"]
    demand = inputs["demand"]

    with duckdb.connect(str(MIP_DB_PATH), read_only=True) as con:

        tables = {
            row[0] for row in con.execute("SHOW TABLES").fetchall()
        }

        if "mip_direct_baseline" not in tables:
            raise ValueError(
                "Step 31 baseline missing. "
                "Run src/mip_truck_allocation.py first."
            )

        baseline_rows = con.execute("""
            SELECT vehicle_type_id, trips, truck_cost_aud
            FROM mip_direct_baseline
            WHERE scenario_id = ?
              AND is_cheapest
        """, [scenario_id]).fetchall()

    if not baseline_rows:
        raise ValueError(
            f"No Step 31 baseline for {scenario_id}. "
            "Run src/mip_truck_allocation.py first."
        )

    baseline_cost = sum(row[2] for row in baseline_rows)
    baseline_trips = sum(row[1] for row in baseline_rows)
    baseline_capacity = sum(
        row[1] * vehicles[row[0]].volume_m3 for row in baseline_rows
    )

    # --------------------------------------------------------
    # 32.4 SOLVE EVERY PERIOD
    # --------------------------------------------------------

    route_options = find_route_options(inputs["states"], lane_km)

    plans = {}
    started = perf_counter()

    for period in sorted(demand):
        plans[period] = solve_period(
            period, demand[period], route_options, vehicles, lane_km
        )

    solve_seconds = perf_counter() - started

    # --------------------------------------------------------
    # 32.5 COLLECT RESULTS
    # --------------------------------------------------------

    trip_rows = []
    route_rows = []

    truck_cost = 0.0
    transfer_cost = 0.0
    total_trips = 0
    carried_volume = 0.0
    dispatched_volume = 0.0
    legs_driven = 0

    direct_lane_periods = 0
    transfer_lane_periods = 0

    vehicle_trips = defaultdict(int)
    hub_parcels = defaultdict(int)

    for period, plan in plans.items():

        truck_cost += plan["truck_cost"]
        transfer_cost += plan["transfer_cost"]
        legs_driven += len(plan["leg_trips"])

        # Volume on every leg. A transferred parcel rides two legs,
        # so it takes up vehicle space twice.
        carried_volume += sum(plan["leg_volume"].values())

        for leg, by_vehicle in plan["leg_trips"].items():

            for vehicle_id, trip_count in by_vehicle.items():

                vehicle = vehicles[vehicle_id]
                cost_per_trip = trip_cost(vehicle, lane_km[leg])

                total_trips += trip_count
                vehicle_trips[vehicle_id] += trip_count
                dispatched_volume += trip_count * vehicle.volume_m3

                trip_rows.append((
                    scenario_id, period, leg[0], leg[1], vehicle_id,
                    trip_count,
                    round(lane_km[leg], 3),
                    round(cost_per_trip, 4),
                    round(trip_count * cost_per_trip, 2),
                ))

        for lane, hub in plan["chosen_hub"].items():

            load = demand[period][lane]

            if hub is None:
                direct_lane_periods += 1
                route_km = lane_km[lane]
                handling = 0.0
            else:
                transfer_lane_periods += 1
                hub_parcels[hub] += load.parcels
                route_km = lane_km[(lane[0], hub)] + lane_km[(hub, lane[1])]
                handling = load.handling_cost_aud

            route_rows.append((
                scenario_id, period, lane[0], lane[1], hub,
                load.parcels,
                round(load.volume_m3, 6),
                round(load.weight_kg, 3),
                round(route_km, 3),
                round(handling, 2),
            ))

    plan_cost = truck_cost + transfer_cost
    saving = baseline_cost - plan_cost

    # Volume counted once per lane, as the direct baseline carries it.
    lane_volume = sum(
        load.volume_m3
        for loads in demand.values()
        for load in loads.values()
    )

    # The baseline uses one vehicle type per lane all year, which
    # the solver is free to copy, so the plan must not cost more.
    if plan_cost > baseline_cost + 0.05:
        raise AssertionError(
            f"Plan (A${plan_cost:,.2f}) costs more than the Step 31 "
            f"baseline (A${baseline_cost:,.2f}). Rerun Steps 30 and 31."
        )

    # --------------------------------------------------------
    # 32.6 SAVE THE PLAN
    # --------------------------------------------------------

    trips_df = pd.DataFrame(trip_rows, columns=[
        "scenario_id", "period", "origin_state", "destination_state",
        "vehicle_type_id", "trips", "modelled_distance_km",
        "cost_per_trip_aud", "total_truck_cost_aud",
    ])

    routes_df = pd.DataFrame(route_rows, columns=[
        "scenario_id", "period", "origin_state", "destination_state",
        "via_state", "parcel_count", "volume_m3", "weight_kg",
        "route_distance_km", "transfer_handling_cost_aud",
    ])

    with duckdb.connect(str(MIP_DB_PATH)) as con:

        # Vehicle trips on each leg, each period.
        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_network_trips (
                scenario_id VARCHAR NOT NULL,
                period INTEGER NOT NULL,
                origin_state VARCHAR NOT NULL,
                destination_state VARCHAR NOT NULL,
                vehicle_type_id VARCHAR NOT NULL,
                trips INTEGER NOT NULL,
                modelled_distance_km DOUBLE NOT NULL,
                cost_per_trip_aud DOUBLE NOT NULL,
                total_truck_cost_aud DOUBLE NOT NULL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    scenario_id, period, origin_state,
                    destination_state, vehicle_type_id
                ),
                CHECK (trips > 0),
                CHECK (total_truck_cost_aud >= 0)
            )
        """)

        # The route each lane's parcels take, each period.
        # via_state is NULL when the lane is sent direct.
        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_network_routes (
                scenario_id VARCHAR NOT NULL,
                period INTEGER NOT NULL,
                origin_state VARCHAR NOT NULL,
                destination_state VARCHAR NOT NULL,
                via_state VARCHAR,
                parcel_count BIGINT NOT NULL,
                volume_m3 DOUBLE NOT NULL,
                weight_kg DOUBLE NOT NULL,
                route_distance_km DOUBLE NOT NULL,
                transfer_handling_cost_aud DOUBLE NOT NULL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    scenario_id, period, origin_state, destination_state
                ),
                CHECK (origin_state <> destination_state)
            )
        """)

        # Remove this scenario's previous plan, then insert the new one.
        con.execute(
            "DELETE FROM mip_network_trips WHERE scenario_id = ?",
            [scenario_id],
        )
        con.execute(
            "DELETE FROM mip_network_routes WHERE scenario_id = ?",
            [scenario_id],
        )

        con.register("new_trips", trips_df)
        con.register("new_routes", routes_df)

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("""
                INSERT INTO mip_network_trips (
                    scenario_id, period, origin_state, destination_state,
                    vehicle_type_id, trips, modelled_distance_km,
                    cost_per_trip_aud, total_truck_cost_aud
                )
                SELECT * FROM new_trips
            """)

            con.execute("""
                INSERT INTO mip_network_routes (
                    scenario_id, period, origin_state, destination_state,
                    via_state, parcel_count, volume_m3, weight_kg,
                    route_distance_km, transfer_handling_cost_aud
                )
                SELECT * FROM new_routes
            """)

            saved_trips, saved_routes = con.execute("""
                SELECT
                    (SELECT COUNT(*) FROM mip_network_trips
                     WHERE scenario_id = ?),
                    (SELECT COUNT(*) FROM mip_network_routes
                     WHERE scenario_id = ?)
            """, [scenario_id, scenario_id]).fetchone()

            if saved_trips != len(trip_rows) or saved_routes != len(route_rows):
                raise AssertionError("Saved row counts do not match the plan.")

            # Record that this scenario now has a solved plan.
            con.execute("""
                UPDATE mip_scenarios
                SET scenario_status = 'Completed'
                WHERE scenario_id = ?
            """, [scenario_id])

            con.execute("COMMIT")

        except Exception:
            con.execute("ROLLBACK")
            raise

    # --------------------------------------------------------
    # 32.7 REPORT
    # --------------------------------------------------------

    parcels = inputs["interstate_parcels"]
    lane_periods = direct_lane_periods + transfer_lane_periods
    period_cost = {
        period: plan["truck_cost"] + plan["transfer_cost"]
        for period, plan in plans.items()
    }
    busiest = max(period_cost, key=period_cost.get)
    quietest = min(period_cost, key=period_cost.get)

    print("\nSTEP 32: LINEHAUL NETWORK PLAN (MILP)")
    print("=" * 76)
    print(f"Scenario: {scenario_id} (demand x{inputs['demand_multiplier']:.2f})")
    print(f"Planning year: {inputs['planning_year']}")
    print(f"Days per departure period: {PERIOD_DAYS}")
    print(f"Periods solved: {len(plans)}, all to proven optimality")
    print(f"Solver: HiGHS, {solve_seconds:.1f} seconds in total")
    print(f"Interstate parcels: {parcels:,}")

    print("\nCOST PER YEAR")
    print("-" * 76)
    print(f"Step 31, every lane direct:       A${baseline_cost:>14,.2f}")
    print(f"Step 32, truck trips:             A${truck_cost:>14,.2f}")
    print(f"Step 32, extra transfer handling: A${transfer_cost:>14,.2f}")
    print(f"Step 32, total:                   A${plan_cost:>14,.2f}")
    print(
        f"Saving from optimisation:         A${saving:>14,.2f} "
        f"({saving / baseline_cost:.1%})"
    )
    print(
        f"Linehaul cost per parcel: A${baseline_cost / parcels:.2f} "
        f"-> A${plan_cost / parcels:.2f}"
    )

    print("\nHOW THE PLAN SAVES MONEY")
    print("-" * 76)
    print(f"Trips per year: {baseline_trips:,} -> {total_trips:,}")
    print(
        f"Average volume fill: "
        f"{lane_volume / baseline_capacity:.1%} -> "
        f"{carried_volume / dispatched_volume:.1%}"
    )
    print(
        f"Legs driven per period: {len(lane_km)} possible, "
        f"{legs_driven / len(plans):.1f} used on average"
    )
    print(
        f"Lane shipments sent direct: {direct_lane_periods:,} "
        f"({direct_lane_periods / lane_periods:.0%})"
    )
    print(
        f"Lane shipments transferred at a hub: {transfer_lane_periods:,} "
        f"({transfer_lane_periods / lane_periods:.0%})"
    )

    print("\nTRANSFER HUBS CHOSEN BY THE SOLVER (PARCELS PER YEAR)")
    print("-" * 76)
    for hub, count in sorted(hub_parcels.items(), key=lambda item: -item[1]):
        print(f"{hub:>3} | {count:>7,} parcels transferred")

    print("\nVEHICLE MIX (TRIPS PER YEAR)")
    print("-" * 76)
    for vehicle_id, count in sorted(
        vehicle_trips.items(), key=lambda item: -item[1]
    ):
        print(f"{vehicle_id:13} | {count:>6,}")

    print("\nSEASONALITY")
    print("-" * 76)
    print(f"Quietest period {quietest:>2}: A${period_cost[quietest]:>10,.2f}")
    print(f"Busiest period  {busiest:>2}: A${period_cost[busiest]:>10,.2f}")

    print(f"\nEXAMPLE: LARGEST TRANSFERRED LANES IN PERIOD {busiest}")
    print("-" * 76)
    transferred = sorted(
        (
            (demand[busiest][lane].volume_m3, lane, hub)
            for lane, hub in plans[busiest]["chosen_hub"].items()
            if hub is not None
        ),
        reverse=True
    )
    for volume, lane, hub in transferred[:6]:
        detour = (
            (lane_km[(lane[0], hub)] + lane_km[(hub, lane[1])])
            / lane_km[lane] - 1
        )
        print(
            f"{lane[0]:>3} -> {lane[1]:<3} goes via {hub:<3} "
            f"| {volume:5.2f} m3 | {detour:+.0%} distance"
        )

    print("\nIMPORTANT LIMITATIONS")
    print("-" * 76)
    print(f"- Parcels wait up to {PERIOD_DAYS} days for a departure. "
          "Delivery promises are not modelled.")
    print("- A transfer adds handling time that is not modelled.")
    print(f"- Vehicles count as full at {MAX_VOLUME_FILL:.0%} of usable volume. "
          "No parcel-level packing.")
    print("- Trips are one-way. Return journeys and fleet size are not modelled.")
    print("- Candidate facility opening is not yet optimised.")

    # --------------------------------------------------------
    # 32.8 RETURN RESULTS
    # --------------------------------------------------------

    return {
        "scenario_id": scenario_id,
        "planning_year": inputs["planning_year"],
        "demand_multiplier": inputs["demand_multiplier"],
        "periods_solved": len(plans),
        "interstate_parcels": parcels,
        "baseline_cost_aud": round(baseline_cost, 2),
        "truck_cost_aud": round(truck_cost, 2),
        "transfer_handling_cost_aud": round(transfer_cost, 2),
        "plan_cost_aud": round(plan_cost, 2),
        "saving_aud": round(saving, 2),
        "saving_pct": round(100 * saving / baseline_cost, 2),
        "total_trips": total_trips,
        "average_volume_fill": round(carried_volume / dispatched_volume, 4),
        "saved_trip_records": len(trip_rows),
        "saved_route_records": len(route_rows),
    }


# ============================================================
# MAIN EXECUTION: SOLVE EACH SCENARIO, THEN COMPARE THEM
# ============================================================

if __name__ == "__main__":

    summaries = [
        optimise_linehaul_network(scenario_id)
        for scenario_id in SCENARIOS_TO_RUN
    ]

    print("\nSCENARIO COMPARISON")
    print("=" * 76)
    print(
        f"{'Scenario':15} | {'Parcels':>9} | {'Direct (A$)':>13} "
        f"| {'Optimised (A$)':>14} | {'Saving':>6} | {'Trips':>6}"
    )
    for summary in summaries:
        print(
            f"{summary['scenario_id']:15} "
            f"| {summary['interstate_parcels']:>9,} "
            f"| {summary['baseline_cost_aud']:>13,.0f} "
            f"| {summary['plan_cost_aud']:>14,.0f} "
            f"| {summary['saving_pct']:>5.1f}% "
            f"| {summary['total_trips']:>6,}"
        )