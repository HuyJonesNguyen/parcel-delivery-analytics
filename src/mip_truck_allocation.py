"""Step 31: Direct-shipping baseline for interstate linehaul.

Run from the project root:
    python src/mip_truck_allocation.py

Prerequisites (run in this order, if not already done):
    python src/mip_facility_registry.py
    python src/mip_transport_costs.py
    python src/mip_parcel_profiles.py
    python src/mip_vehicle_registry.py
    python src/mip_demand.py

The baseline is the rule a planner could apply without a solver:

    "Every lane gets its own vehicle. Each period, send enough
     trips of that vehicle type to carry the lane's parcels,
     straight from origin to destination."

For every lane the script costs that rule with each vehicle type
and marks the cheapest. Step 32 must beat this number.

All parameters are synthetic. Trips are one-way. Return journeys
and fleet availability are not modelled.

Next step:
    python src/mip_optimization.py
"""

import duckdb

from mip_common import (
    DISTANCE_MULTIPLIER,
    MAX_VOLUME_FILL,
    MIP_DB_PATH,
    PERIOD_DAYS,
    SCENARIOS_TO_RUN,
    load_linehaul_inputs,
    trip_cost,
    trips_needed,
)


# ============================================================
# STEP 31: CALCULATE AND STORE THE DIRECT-SHIPPING BASELINE
# ============================================================

def calculate_direct_baseline(scenario_id):
    """Cost direct shipping with one vehicle type per lane.

    For a lane with volume V and weight W in a period, the trips
    needed with vehicle type t are

        max( ceil(V / usable volume of t), ceil(W / payload of t) )

    Annual cost for (lane, t) = sum over periods of trips x trip cost.
    The cheapest t per lane is the baseline.
    """

    # --------------------------------------------------------
    # 31.1 READ AND VALIDATE INPUTS
    # --------------------------------------------------------

    inputs = load_linehaul_inputs(scenario_id)

    vehicles = inputs["vehicles"]
    lane_km = inputs["lane_km"]
    demand = inputs["demand"]

    # --------------------------------------------------------
    # 31.2 COST EVERY VEHICLE TYPE ON EVERY LANE
    # --------------------------------------------------------

    # totals[(lane, vehicle_id)] = [departures, trips, cost, capacity m3]
    totals = {}
    lane_volume = {}

    for period in sorted(demand):

        for lane, load in demand[period].items():

            lane_volume[lane] = lane_volume.get(lane, 0.0) + load.volume_m3

            for vehicle_id, vehicle in vehicles.items():

                trips = trips_needed(load.volume_m3, load.weight_kg, vehicle)
                if trips == 0:
                    continue

                record = totals.setdefault((lane, vehicle_id), [0, 0, 0.0, 0.0])
                record[0] += 1
                record[1] += trips
                record[2] += trips * trip_cost(vehicle, lane_km[lane])
                record[3] += trips * vehicle.volume_m3

    # --------------------------------------------------------
    # 31.3 PICK THE CHEAPEST VEHICLE TYPE PER LANE
    # --------------------------------------------------------

    # Ties are broken by vehicle ID so reruns give the same answer.
    cheapest = {}
    for (lane, vehicle_id), (_, _, cost, _) in totals.items():
        best = cheapest.get(lane)
        if best is None or (cost, vehicle_id) < best:
            cheapest[lane] = (cost, vehicle_id)

    rows = []
    for (lane, vehicle_id), (departures, trips, cost, capacity) in sorted(
        totals.items()
    ):
        origin, destination = lane
        vehicle = vehicles[vehicle_id]

        rows.append((
            scenario_id,
            origin,
            destination,
            vehicle_id,
            departures,
            trips,
            round(lane_km[lane], 3),
            round(trip_cost(vehicle, lane_km[lane]), 4),
            round(cost, 2),
            round(100 * lane_volume[lane] / capacity, 2),
            cheapest[lane][1] == vehicle_id,
        ))

    # --------------------------------------------------------
    # 31.4 SAVE, REPLACING THIS SCENARIO'S PREVIOUS ROWS
    # --------------------------------------------------------

    with duckdb.connect(str(MIP_DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_direct_baseline (
                scenario_id VARCHAR NOT NULL,
                origin_state VARCHAR NOT NULL,
                destination_state VARCHAR NOT NULL,
                vehicle_type_id VARCHAR NOT NULL,
                departures INTEGER NOT NULL,
                trips INTEGER NOT NULL,
                modelled_distance_km DOUBLE NOT NULL,
                cost_per_trip_aud DOUBLE NOT NULL,
                truck_cost_aud DOUBLE NOT NULL,
                volume_fill_pct DOUBLE NOT NULL,
                is_cheapest BOOLEAN NOT NULL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    scenario_id, origin_state,
                    destination_state, vehicle_type_id
                ),
                CHECK (origin_state <> destination_state),
                CHECK (trips > 0),
                CHECK (truck_cost_aud >= 0)
            )
        """)

        con.execute(
            "DELETE FROM mip_direct_baseline WHERE scenario_id = ?",
            [scenario_id],
        )

        con.executemany("""
            INSERT INTO mip_direct_baseline (
                scenario_id, origin_state, destination_state,
                vehicle_type_id, departures, trips,
                modelled_distance_km, cost_per_trip_aud,
                truck_cost_aud, volume_fill_pct, is_cheapest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        saved = con.execute("""
            SELECT COUNT(*) FROM mip_direct_baseline
            WHERE scenario_id = ?
        """, [scenario_id]).fetchone()[0]

    if saved != len(rows):
        raise ValueError(f"Saved {saved} baseline rows; expected {len(rows)}")

    # --------------------------------------------------------
    # 31.5 REPORT
    # --------------------------------------------------------

    baseline_rows = [row for row in rows if row[-1]]

    baseline_cost = sum(row[8] for row in baseline_rows)
    baseline_trips = sum(row[5] for row in baseline_rows)

    carried_volume = sum(lane_volume[(row[1], row[2])] for row in baseline_rows)
    dispatched_volume = sum(
        row[5] * vehicles[row[3]].volume_m3 for row in baseline_rows
    )

    vehicle_choice = {}
    for row in baseline_rows:
        vehicle_choice[row[3]] = vehicle_choice.get(row[3], 0) + 1

    print("\nSTEP 31: DIRECT-SHIPPING BASELINE")
    print("-" * 75)
    print(f"Scenario: {scenario_id} (demand x{inputs['demand_multiplier']:.2f})")
    print(f"Days per departure period: {PERIOD_DAYS}")
    print(f"Interstate parcels: {inputs['interstate_parcels']:,}")
    print(f"Intrastate parcels excluded from linehaul: {inputs['intrastate_parcels']:,}")
    print(f"Interstate lanes: {len(baseline_rows)}")
    print(f"Route-length multiplier: {DISTANCE_MULTIPLIER:.2f} (synthetic)")
    print(f"Packing allowance: vehicles count as full at {MAX_VOLUME_FILL:.0%}")

    print("\nRULE: ONE VEHICLE TYPE PER LANE, SENT DIRECT EVERY PERIOD")
    print(f"Trips per year: {baseline_trips:,}")
    print(f"Truck cost per year: A${baseline_cost:,.2f}")
    print(
        f"Truck cost per interstate parcel: "
        f"A${baseline_cost / inputs['interstate_parcels']:.2f}"
    )
    print(f"Average volume fill: {carried_volume / dispatched_volume:.1%}")

    print("\nCHEAPEST VEHICLE TYPE, BY NUMBER OF LANES")
    for vehicle_id, lane_count in sorted(
        vehicle_choice.items(), key=lambda item: -item[1]
    ):
        print(f"{vehicle_id:13} | {lane_count:>2} lanes")

    print("\nMOST EXPENSIVE LANES UNDER THIS RULE")
    for row in sorted(baseline_rows, key=lambda row: -row[8])[:5]:
        print(
            f"{row[1]:>3} -> {row[2]:<3} | {row[3]:12} "
            f"| Trips: {row[5]:>4,} | Fill: {row[9]:5.1f}% "
            f"| Cost: A${row[8]:>11,.2f}"
        )

    print("\nWHY THIS IS A WEAK PLAN")
    print("- Each lane runs its own vehicle, however little it carries.")
    print("- Step 32 lets lanes share vehicles by transferring at a hub.")

    return {
        "scenario_id": scenario_id,
        "interstate_parcels": inputs["interstate_parcels"],
        "interstate_lanes": len(baseline_rows),
        "baseline_trips": baseline_trips,
        "baseline_truck_cost_aud": round(baseline_cost, 2),
        "average_volume_fill": round(carried_volume / dispatched_volume, 4),
    }


# ============================================================
# MAIN EXECUTION: RUN STEP 31 FOR EACH SCENARIO
# ============================================================

if __name__ == "__main__":

    baseline_summaries = [
        calculate_direct_baseline(scenario_id)
        for scenario_id in SCENARIOS_TO_RUN
    ]