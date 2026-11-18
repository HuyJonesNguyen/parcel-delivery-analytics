"""
Step 33: Which candidate facility should take the overflow?

Run:
    python src/mip_facility_opening.py

Prerequisites:
    python src/mip_facility_registry.py   (facilities)
    python src/mip_transport_costs.py     (distances, candidates included)
    python src/mip_parcel_profiles.py
    python src/mip_vehicle_registry.py
    python src/mip_demand.py              (demand pattern by period)

THE BUSINESS QUESTION
    Plan for N_PARCELS parcels a year (set in mip_common.py).
    Spread over the existing network, that many parcels push some
    facilities past their yearly capacity. Melbourne is one.

    The overflow has to go to a candidate facility in the same
    state. Each candidate has a yearly fixed cost, and each one
    changes the truck bill, because it sends its own interstate
    vehicles from its own location and so shares vehicles less.

    Which candidate gives the lowest fixed cost + extra truck cost?

HOW IT WORKS
    1. Scale the historical demand pattern to N_PARCELS.
    2. Compare each Active facility's yearly parcels with its
       yearly capacity. The excess is the overflow. Overload is
       taken as a fact here. Delays and queues are not modelled.
    3. For each overloaded state, list the candidate options that
       are big enough to take the overflow.
    4. Price the trucks for each option with the Step 32 MILP,
       with the candidate added to the network as an extra site
       carrying its share of every lane.
    5. Score = fixed cost + extra truck cost. Lowest score wins.

All capacities and costs are synthetic assumptions.
"""

from itertools import combinations
from time import perf_counter

import duckdb
import pandas as pd

from mip_common import (
    CANDIDATE_COST_PER_CAPACITY_AUD,
    COUNT_ARRIVALS_IN_WORKLOAD,
    DISTANCE_MULTIPLIER,
    MAX_DETOUR_RATIO,
    MIP_DB_PATH,
    N_PARCELS,
    PERIOD_DAYS,
    TRUCK_COST_EVERY_NTH_PERIOD,
    Load,
    Vehicle,
)
from mip_optimization import solve_period


# ============================================================
# STEP 33.1: LOAD THE NETWORK AND SCALE DEMAND TO N_PARCELS
# ============================================================

def load_inputs():
    """Read facilities, vehicles, distances and the demand pattern.

    The demand pattern is the historical one stored by Step 30.
    Every figure is multiplied by one factor so that the yearly
    total equals N_PARCELS.
    """

    if not isinstance(N_PARCELS, int) or N_PARCELS <= 0:
        raise ValueError("N_PARCELS must be a positive whole number.")

    if not MIP_DB_PATH.is_file():
        raise FileNotFoundError(
            f"MIP database not found: {MIP_DB_PATH}. "
            "Run src/mip_facility_registry.py first."
        )

    with duckdb.connect(str(MIP_DB_PATH), read_only=True) as con:

        facility_rows = con.execute("""
            SELECT facility_id, facility_name, state, status,
                   annual_capacity, annual_fixed_cost_aud
            FROM facility_registry
            WHERE status IN ('Active', 'Candidate')
            ORDER BY state, status, facility_id
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

        # Any stored scenario has the same pattern. Use one of them.
        pattern = con.execute("""
            SELECT scenario_id
            FROM mip_scenarios
            WHERE scenario_id IN (
                SELECT DISTINCT scenario_id FROM mip_demand_by_period
            )
            ORDER BY ABS(demand_multiplier - 1.0), scenario_id
            LIMIT 1
        """).fetchone()

        if pattern is None:
            raise ValueError("No demand stored. Run src/mip_demand.py first.")

        demand_rows = con.execute("""
            SELECT period, origin_state, destination_state,
                   SUM(parcel_count), SUM(total_volume_m3),
                   SUM(total_weight_kg), SUM(total_handling_cost_aud)
            FROM mip_demand_by_period
            WHERE scenario_id = ?
            GROUP BY period, origin_state, destination_state
        """, [pattern[0]]).fetchall()

    if not vehicle_rows or not distance_rows:
        raise ValueError("Vehicle types or distances missing. Run Steps 26 and 29.")

    # --------------------------------------------------------
    # Scale factor: stored parcels -> N_PARCELS
    # --------------------------------------------------------

    stored_parcels = sum(row[3] for row in demand_rows)
    factor = N_PARCELS / stored_parcels

    # demand[period][(origin_state, destination_state)] = Load
    # Interstate lanes only: these are what the trucks carry.
    demand = {}

    # Yearly parcels each state's facilities must sort.
    workload = {}

    for period, origin, destination, parcels, volume, weight, handling in demand_rows:

        parcels = float(parcels) * factor

        workload[origin] = workload.get(origin, 0.0) + parcels

        if origin == destination:
            continue

        if COUNT_ARRIVALS_IN_WORKLOAD:
            workload[destination] = workload.get(destination, 0.0) + parcels

        demand.setdefault(int(period), {})[(origin, destination)] = Load(
            parcels,
            float(volume) * factor,
            float(weight) * factor,
            float(handling) * factor,
        )

    # --------------------------------------------------------
    # Facilities, with the cost rule for un-costed candidates
    # --------------------------------------------------------

    active_rows = [row for row in facility_rows if row[3] == "Active"]

    if CANDIDATE_COST_PER_CAPACITY_AUD is None:
        cost_rate = (
            sum(row[5] for row in active_rows)
            / sum(row[4] for row in active_rows)
        )
    else:
        cost_rate = float(CANDIDATE_COST_PER_CAPACITY_AUD)

    active = {}       # {state: facility record}
    candidates = {}   # {facility_id: facility record}

    for facility_id, name, state, status, capacity, fixed_cost in facility_rows:

        record = {
            "facility_id": facility_id,
            "name": name,
            "state": state,
            "capacity": float(capacity),
            "fixed_cost": float(fixed_cost),
            "cost_source": "Registry",
        }

        if status == "Active":
            if state in active:
                raise ValueError(f"More than one Active facility in {state}.")
            active[state] = record
        else:
            if fixed_cost == 0:
                # 0 in the registry means "not yet estimated".
                record["fixed_cost"] = cost_rate * capacity
                record["cost_source"] = "Estimated from rate"
            candidates[facility_id] = record

    missing = sorted(set(workload) - set(active))
    if missing:
        raise ValueError(f"States with parcels but no Active facility: {missing}")

    vehicles = {
        vehicle_id: Vehicle(float(volume), float(payload), float(dispatch), float(per_km))
        for vehicle_id, volume, payload, dispatch, per_km in vehicle_rows
    }

    facility_km = {
        (origin_id, destination_id): float(km) * DISTANCE_MULTIPLIER
        for origin_id, destination_id, km in distance_rows
    }

    return {
        "demand": demand,
        "workload": workload,
        "active": active,
        "candidates": candidates,
        "vehicles": vehicles,
        "facility_km": facility_km,
        "factor": factor,
        "stored_parcels": stored_parcels,
        "cost_rate": cost_rate,
    }


# ============================================================
# STEP 33.2: FIND THE OVERFLOW
# ============================================================

def find_overflow(inputs):
    """Compare each state's yearly parcels with its Active capacity.

    Returns {state: {"workload", "capacity", "overflow", "share"}}
    for every state. "share" is the part of the state's parcels
    that must move to a candidate (0 when the facility copes).
    """

    result = {}

    for state, parcels in inputs["workload"].items():

        capacity = inputs["active"][state]["capacity"]
        overflow = max(0.0, parcels - capacity)

        result[state] = {
            "workload": parcels,
            "capacity": capacity,
            "overflow": overflow,
            "share": overflow / parcels if parcels > 0 else 0.0,
        }

    return result


# ============================================================
# STEP 33.3: LIST THE CANDIDATE OPTIONS FOR ONE STATE
# ============================================================

def candidate_options(state, overflow, candidates):
    """Return the sets of candidates that can take a state's overflow.

    An option is one candidate, or several together when no single
    one is big enough. An option that contains a smaller option
    which already works is skipped: it only adds fixed cost.
    """

    in_state = sorted(
        f for f, record in candidates.items() if record["state"] == state
    )

    options = []

    for size in range(1, len(in_state) + 1):
        for group in combinations(in_state, size):

            if sum(candidates[f]["capacity"] for f in group) < overflow:
                continue

            if any(set(smaller) < set(group) for smaller in options):
                continue

            options.append(group)

    return options


# ============================================================
# STEP 33.4: PRICE THE TRUCKS FOR A GIVEN SET OF OPEN CANDIDATES
# ============================================================

def truck_cost_per_year(inputs, opened_by_state, overflow):
    """Yearly truck cost when the given candidates are open.

    opened_by_state = {state: (candidate ids)}. An empty dictionary
    prices the existing network as if capacity were unlimited,
    which is the reference every option is compared with.

    A site is named by its state code when it is the Active
    facility ("VIC") and by its facility ID when it is a candidate
    ("VIC_H10").
    """

    active = inputs["active"]
    candidates = inputs["candidates"]
    facility_km = inputs["facility_km"]

    # --------------------------------------------------------
    # 33.4.1 HOW EACH STATE'S PARCELS ARE SPLIT BETWEEN SITES
    # --------------------------------------------------------

    # sites[state] = [(site name, share of the state's parcels), ...]
    sites = {state: [(state, 1.0)] for state in active}

    for state, group in opened_by_state.items():

        share = overflow[state]["share"]
        group_capacity = sum(candidates[f]["capacity"] for f in group)

        # The Active facility keeps what it can hold. The candidates
        # share the rest in proportion to their capacity.
        sites[state] = [(state, 1.0 - share)] + [
            (f, share * candidates[f]["capacity"] / group_capacity)
            for f in group
        ]

    def facility_id(site):
        return active[site]["facility_id"] if site in active else site

    def home_state(site):
        return site if site in active else candidates[site]["state"]

    all_sites = [site for group in sites.values() for site, _ in group]

    # --------------------------------------------------------
    # 33.4.2 DISTANCES AND ROUTE OPTIONS BETWEEN SITES
    # --------------------------------------------------------

    lane_km = {}

    for origin in all_sites:
        for destination in all_sites:
            if home_state(origin) == home_state(destination):
                continue
            lane_km[(origin, destination)] = facility_km[
                (facility_id(origin), facility_id(destination))
            ]

    # Only Active facilities act as transfer hubs. A parcel never
    # transfers at the Active facility of its own state: that
    # facility is the one being relieved.
    route_options = {}

    for lane, direct_km in lane_km.items():

        origin, destination = lane
        blocked = {home_state(origin), home_state(destination)}

        hubs = [
            hub for hub in active
            if hub not in blocked
            and lane_km[(origin, hub)] + lane_km[(hub, destination)]
            <= MAX_DETOUR_RATIO * direct_km
        ]

        route_options[lane] = [None] + hubs

    # --------------------------------------------------------
    # 33.4.3 SOLVE A SAMPLE OF PERIODS WITH THE STEP 32 MODEL
    # --------------------------------------------------------

    periods = sorted(inputs["demand"])
    sampled = periods[::TRUCK_COST_EVERY_NTH_PERIOD]

    total = 0.0

    for period in sampled:

        # Split every state-to-state load between the open sites.
        loads = {}

        for (origin, destination), load in inputs["demand"][period].items():
            for origin_site, origin_share in sites[origin]:
                for destination_site, destination_share in sites[destination]:

                    part = origin_share * destination_share
                    if part <= 0:
                        continue

                    loads[(origin_site, destination_site)] = Load(
                        load.parcels * part,
                        load.volume_m3 * part,
                        load.weight_kg * part,
                        load.handling_cost_aud * part,
                    )

        plan = solve_period(
            period, loads, route_options, inputs["vehicles"], lane_km
        )

        total += plan["truck_cost"] + plan["transfer_cost"]

    # Scale the sample up to the whole year.
    return total * len(periods) / len(sampled)


# ============================================================
# STEP 33: COMPARE THE CANDIDATES, SAVE AND REPORT
# ============================================================

def choose_candidate_facilities():
    """Find the overflow and the cheapest candidate option per state."""

    started = perf_counter()

    inputs = load_inputs()
    candidates = inputs["candidates"]
    overflow = find_overflow(inputs)

    overloaded = sorted(
        (state for state, o in overflow.items() if o["overflow"] > 0),
        key=lambda state: -overflow[state]["overflow"]
    )

    # --------------------------------------------------------
    # 33.5 REPORT THE WORKLOAD FIRST
    # --------------------------------------------------------

    print("\nSTEP 33: CANDIDATE FACILITY CHOICE")
    print("=" * 78)
    print(f"Parcels to plan for (N_PARCELS): {N_PARCELS:,}")
    print(
        f"Historical pattern: {inputs['stored_parcels']:,.0f} parcels, "
        f"scaled x{inputs['factor']:.3f}"
    )

    print("\n1. YEARLY PARCELS AGAINST YEARLY CAPACITY")
    print("-" * 78)
    print(
        f"{'State':5} | {'Facility':10} | {'Parcels':>9} | {'Capacity':>9} | "
        f"{'Used':>6} | {'Overflow':>9} | {'Goes over at N =':>16}"
    )

    for state in sorted(overflow, key=lambda s: -overflow[s]["workload"]):
        o = overflow[state]
        limit_n = o["capacity"] / o["workload"] * N_PARCELS
        print(
            f"{state:5} | {inputs['active'][state]['name'][:10]:10} | "
            f"{o['workload']:>9,.0f} | {o['capacity']:>9,.0f} | "
            f"{o['workload'] / o['capacity']:>6.0%} | {o['overflow']:>9,.0f} | "
            f"{limit_n:>16,.0f}"
        )

    if not overloaded:
        print("\nNo facility is over capacity. No candidate is needed.")
        print("Raise N_PARCELS in mip_common.py to test a larger network.")
        return {"n_parcels": N_PARCELS, "overloaded_states": [], "choices": {}}

    # --------------------------------------------------------
    # 33.6 PRICE THE EXISTING NETWORK (THE REFERENCE)
    # --------------------------------------------------------

    reference_cost = truck_cost_per_year(inputs, {}, overflow)

    # --------------------------------------------------------
    # 33.7 PRICE EVERY OPTION, ONE STATE AT A TIME
    # --------------------------------------------------------

    rows = []
    choices = {}
    no_option = []

    for state in overloaded:

        options = candidate_options(
            state, overflow[state]["overflow"], candidates
        )

        if not options:
            no_option.append(state)
            continue

        scored = []

        for group in options:

            option_cost = truck_cost_per_year(inputs, {state: group}, overflow)

            fixed_cost = sum(candidates[f]["fixed_cost"] for f in group)
            extra_truck = option_cost - reference_cost

            scored.append({
                "state": state,
                "group": group,
                "capacity": sum(candidates[f]["capacity"] for f in group),
                "fixed_cost": fixed_cost,
                "truck_cost": option_cost,
                "extra_truck_cost": extra_truck,
                "score": fixed_cost + extra_truck,
            })

        best = min(scored, key=lambda option: (option["score"], option["group"]))
        choices[state] = best

        for option in scored:
            option["is_best"] = option is best
            rows.append(option)

    # --------------------------------------------------------
    # 33.8 PRICE THE FINAL NETWORK WITH EVERY CHOSEN CANDIDATE
    # --------------------------------------------------------

    final_truck_cost = truck_cost_per_year(
        inputs,
        {state: best["group"] for state, best in choices.items()},
        overflow,
    )

    final_fixed_cost = sum(best["fixed_cost"] for best in choices.values())

    # --------------------------------------------------------
    # 33.9 SAVE
    # --------------------------------------------------------

    table_rows = [
        (
            N_PARCELS,
            option["state"],
            " + ".join(option["group"]),
            round(overflow[option["state"]]["overflow"], 1),
            round(option["capacity"], 1),
            round(option["fixed_cost"], 2),
            round(reference_cost, 2),
            round(option["truck_cost"], 2),
            round(option["extra_truck_cost"], 2),
            round(option["score"], 2),
            option["is_best"],
        )
        for option in rows
    ]

    with duckdb.connect(str(MIP_DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_candidate_comparison (
                n_parcels BIGINT NOT NULL,
                state VARCHAR NOT NULL,
                candidates_opened VARCHAR NOT NULL,
                overflow_parcels DOUBLE NOT NULL,
                option_capacity DOUBLE NOT NULL,
                fixed_cost_aud DOUBLE NOT NULL,
                reference_truck_cost_aud DOUBLE NOT NULL,
                option_truck_cost_aud DOUBLE NOT NULL,
                extra_truck_cost_aud DOUBLE NOT NULL,
                total_extra_cost_aud DOUBLE NOT NULL,
                is_best BOOLEAN NOT NULL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (n_parcels, state, candidates_opened)
            )
        """)

        con.execute(
            "DELETE FROM mip_candidate_comparison WHERE n_parcels = ?",
            [N_PARCELS],
        )

        if table_rows:
            comparison_df = pd.DataFrame(table_rows, columns=[
                "n_parcels", "state", "candidates_opened", "overflow_parcels",
                "option_capacity", "fixed_cost_aud", "reference_truck_cost_aud",
                "option_truck_cost_aud", "extra_truck_cost_aud",
                "total_extra_cost_aud", "is_best",
            ])
            con.register("new_comparison", comparison_df)
            con.execute("""
                INSERT INTO mip_candidate_comparison (
                    n_parcels, state, candidates_opened, overflow_parcels,
                    option_capacity, fixed_cost_aud, reference_truck_cost_aud,
                    option_truck_cost_aud, extra_truck_cost_aud,
                    total_extra_cost_aud, is_best
                )
                SELECT * FROM new_comparison
            """)

    # --------------------------------------------------------
    # 33.10 REPORT THE COMPARISON
    # --------------------------------------------------------

    print("\n2. TRUCK COST OF THE EXISTING NETWORK (THE REFERENCE)")
    print("-" * 78)
    print(
        f"A${reference_cost:,.0f} a year if every facility could take "
        "all of its parcels."
    )

    print("\n3. CANDIDATE OPTIONS, CHEAPEST FIRST")
    print("-" * 78)

    for state in overloaded:

        o = overflow[state]
        print(
            f"\n{state}: {o['overflow']:,.0f} parcels over capacity "
            f"({o['share']:.1%} of the state's parcels move to a candidate)"
        )

        if state in no_option:
            print("  No candidate, alone or combined, is big enough.")
            continue

        print(
            f"  {'Option':28} | {'Capacity':>8} | {'Fixed cost':>10} | "
            f"{'Extra trucks':>12} | {'Total':>10}"
        )

        for option in sorted(
            (row for row in rows if row["state"] == state),
            key=lambda row: row["score"]
        ):
            names = " + ".join(candidates[f]["name"] for f in option["group"])
            marker = "  <- best" if option["is_best"] else ""
            print(
                f"  {names[:28]:28} | {option['capacity']:>8,.0f} | "
                f"{option['fixed_cost']:>10,.0f} | "
                f"{option['extra_truck_cost']:>12,.0f} | "
                f"{option['score']:>10,.0f}{marker}"
            )

        too_small = sorted(
            f for f, record in candidates.items()
            if record["state"] == state and record["capacity"] < o["overflow"]
        )
        if too_small:
            print(f"  Too small on their own: {', '.join(too_small)}")

    print("\n4. THE RESULTING NETWORK")
    print("-" * 78)
    for state, best in choices.items():
        print(f"{state}: open {' + '.join(best['group'])}")
    print(f"Fixed cost of opened candidates: A${final_fixed_cost:>12,.0f} a year")
    print(f"Truck cost, existing network:    A${reference_cost:>12,.0f} a year")
    print(f"Truck cost, with the candidates: A${final_truck_cost:>12,.0f} a year")
    print(
        f"Total extra cost of the overflow: "
        f"A${final_fixed_cost + final_truck_cost - reference_cost:,.0f} a year"
    )

    estimated = sorted(
        f for f, record in candidates.items()
        if record["cost_source"] != "Registry"
    )
    if estimated:
        print(
            f"\nFixed cost estimated at A${inputs['cost_rate']:.4f} per parcel "
            f"of yearly capacity for:\n  {', '.join(estimated)}"
        )

    print("\nIMPORTANT LIMITATIONS")
    print("-" * 78)
    print("- Overload is taken as given. Delays and queues are not modelled.")
    print("- A candidate takes the same share of every lane and every week.")
    print("- Options are priced one state at a time, then once all together.")
    print(
        f"- Truck cost comes from every {TRUCK_COST_EVERY_NTH_PERIOD}th "
        f"{PERIOD_DAYS}-day period, scaled to a year."
    )
    print("- Fixed costs of the eight Active facilities are paid either way.")
    print(f"\nRun time: {perf_counter() - started:.0f} seconds")

    return {
        "n_parcels": N_PARCELS,
        "overloaded_states": overloaded,
        "reference_truck_cost_aud": round(reference_cost, 2),
        "final_truck_cost_aud": round(final_truck_cost, 2),
        "final_fixed_cost_aud": round(final_fixed_cost, 2),
        "choices": {
            state: {
                "candidates": list(best["group"]),
                "fixed_cost_aud": round(best["fixed_cost"], 2),
                "extra_truck_cost_aud": round(best["extra_truck_cost"], 2),
                "total_extra_cost_aud": round(best["score"], 2),
            }
            for state, best in choices.items()
        },
        "states_without_option": no_option,
    }


# ============================================================
# MAIN EXECUTION
# ============================================================

if __name__ == "__main__":
    candidate_summary = choose_candidate_facilities()