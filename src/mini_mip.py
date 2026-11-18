import pulp

# 1. DATA
volume = {("NT", "TAS"): 2.0, ("NT", "SA"): 3.0, ("SA", "TAS"): 2.0}       # m3 per week
trip_cost = {("NT", "TAS"): 3000, ("NT", "SA"): 2400, ("SA", "TAS"): 1000}  # AUD per van trip
VAN_M3 = 6.8          # 8 m3 van, full at 85%
TRANSFER_COST = 20    # extra handling if NT -> TAS parcels change vans in SA

# Each lane's route options. A route is the list of legs it drives.
routes = {
    ("NT", "TAS"): {"direct": [("NT", "TAS")],
                    "via_SA": [("NT", "SA"), ("SA", "TAS")]},
    ("NT", "SA"):  {"direct": [("NT", "SA")]},
    ("SA", "TAS"): {"direct": [("SA", "TAS")]},
}

model = pulp.LpProblem("mini_linehaul", pulp.LpMinimize)

# 2. DECISION VARIABLES
use_route = {(lane, r): model.add_variable(f"route_{lane[0]}_{lane[1]}_{r}", cat=pulp.LpBinary)
             for lane in routes for r in routes[lane]}
trips = {leg: model.add_variable(f"trips_{leg[0]}_{leg[1]}", lowBound=0, cat=pulp.LpInteger)
         for leg in trip_cost}

# 3. OBJECTIVE
model += (pulp.lpSum(trip_cost[leg] * trips[leg] for leg in trips)
          + TRANSFER_COST * use_route[(("NT", "TAS"), "via_SA")])

# 4. CONSTRAINTS
for lane in routes:                      # every lane takes exactly one route
    model += pulp.lpSum(use_route[(lane, r)] for r in routes[lane]) == 1
for leg in trips:                        # vans on a leg must hold what is routed over it
    model += VAN_M3 * trips[leg] >= pulp.lpSum(
        volume[lane] * use_route[(lane, r)]
        for lane in routes for r in routes[lane] if leg in routes[lane][r])

# 5. SOLVE AND READ
result = model.solve(pulp.HiGHS(msg=False, gapRel=0))
print(result.status_str, pulp.value(model.objective))
for key, var in use_route.items():
    if pulp.value(var) > 0.5:
        print("route", key)
for leg, var in trips.items():
    print("trips", leg, int(round(pulp.value(var))))
