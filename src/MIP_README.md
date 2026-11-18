# Parcel network optimisation

A synthetic Australian parcel network of eight state facilities, used to answer two planning questions with mixed integer programming:

1. How should interstate parcels travel for the lowest truck cost?
2. Which new facility should open when an existing one runs out of capacity?

> All parcels, costs, capacities and locations are simulated for a portfolio demonstration. They are not Australia Post data or operating figures.

## Results at a glance

| Figure | Meaning |
| --- | --- |
| 500,000 | Synthetic parcels lodged in 2025, with 2,000,000 tracking events |
| 64% | Lower interstate truck cost than sending every lane direct |
| A$230,959 a year | Cheapest candidate to take Melbourne's overflow at 900,000 parcels |

## What the project does

The project runs as a sequence of Python scripts over two DuckDB databases. Each script reads what the previous one stored and adds its own tables.

1. `generate_data.py` simulates a year of parcels between eight facilities, one per state and territory.
2. `load_database.py` loads the parcels, facilities and tracking events into `parcel_delivery.duckdb`.
3. `build_dashboard.py` and `executive_dashboard.py` report delivery performance.
4. The `mip_*.py` scripts build a second database, `mip_network.duckdb`, with facility costs, vehicle types, weekly demand and the two optimisation models.

## The data

Of the 500,000 parcels, 175,496 cross a state border and need interstate linehaul. The rest stay inside one state.

69.68% of parcels arrived on or before their promised date. Parcels lodged in November and December were late 39.83% of the time, against 27.17% in the other ten months, because the simulation adds volume and delay in the peak season.

Each parcel has a recorded weight. The models sort parcels into four sizes by weight (under 1 kg, 1 to 3 kg, 3 to 10 kg, and heavier) and give each size a volume, because the data has no dimensions.

## Question 1: how should interstate parcels travel?

There are 56 interstate lanes, such as NSW to VIC. An average lane carries about 1.4 m3 of parcels a week. The smallest vehicle, a van, holds 8 m3. A vehicle on every lane therefore moves mostly air.

The optimised plan lets a lane's parcels transfer once at another state's facility, so several lanes share one vehicle. Darwin to Hobart parcels ride the Darwin to Adelaide van, change over in Adelaide, and continue with Adelaide's own Hobart parcels.

![Interstate truck cost per year, direct shipping against the optimised network](figures/mip_linehaul_cost.png)

| Per year, 500,000 parcels, weekly departures | Every lane direct (Step 31) | Optimised network (Step 32) |
| --- | ---: | ---: |
| Interstate truck cost (AUD) | 4,252,274 | 1,523,250 |
| Vehicle trips | 2,842 | 1,420 |
| Average volume fill | 16.6% | 43.1% |
| Cost per interstate parcel (AUD) | 24.23 | 8.68 |

![Map of the eight facilities and the legs the optimised plan drives in most weeks](figures/mip_network_map.png)

The plan drives 12 connections in most weeks, where direct shipping drives all 28. The solver chose Canberra and Adelaide as transfer hubs without being told to: about 43,000 parcels a year change vehicles in Canberra and about 35,000 in Adelaide.

With 25% more parcels the plan costs A$1,589,371, only 4.3% more, because the vehicles already have spare room.

### How the model is written

| Part | Description |
| --- | --- |
| Decisions | For each lane, one route: direct, or via one other facility. For each leg and vehicle type, a whole number of trips. |
| Objective | Lowest total of trip costs (a dispatch fee plus a cost per km) and a second handling charge for every parcel that transfers. |
| Rules | Every lane takes exactly one route. The vehicles on a leg must have enough volume and enough payload for all parcels routed over it. |
| Solved | Once per week, 52 times per scenario, with PuLP and the HiGHS solver. A week takes a few seconds. |

A lane has up to seven route options and the best one depends on what the other lanes choose, because they share vehicles. That is why this needs a solver.

`src/mini_mip.py` is the same model with three facilities and one van type. It is the quickest way to read how the variables, objective and constraints are coded.

## Question 2: which candidate facility should open?

Set the yearly parcel count with `N_PARCELS` in `src/mip_common.py`. The parcels are spread over the network in the same proportions as the 2025 data. Each facility can sort 300,000 parcels a year. Sydney goes over that above about 706,000 parcels in total, and Melbourne above about 833,000.

At 900,000 parcels Melbourne is 24,184 parcels over capacity. That overflow, 7.5% of Victoria's parcels, has to go to a candidate site. Each candidate has a yearly fixed cost, and each adds truck cost because it sends its own interstate vehicles and shares them less.

![Yearly cost of each candidate facility, split into fixed cost and extra truck cost](figures/mip_candidate_costs.png)

| State | Candidate | Yearly capacity | Fixed cost (AUD) | Extra truck cost (AUD) | Total (AUD) |
| --- | --- | ---: | ---: | ---: | ---: |
| VIC | **Dandenong South (best)** | 80,000 | 66,667 | 164,293 | 230,959 |
| VIC | Truganina | 120,000 | 100,000 | 164,890 | 264,890 |
| VIC | Melbourne West | 35,000 | 637,000 | 165,007 | 802,007 |
| NSW | **Eastern Creek (best)** | 120,000 | 100,000 | 155,181 | 255,181 |

The extra truck cost is between A$164,293 and A$165,007 for all three Victorian candidates, because they sit within about 32 km of Melbourne. Fixed cost decides the ranking.

Sydney is 82,309 parcels over capacity at the same volume. Eastern Creek is the only NSW candidate large enough to take that on its own. With Dandenong South and Eastern Creek both open, truck cost rises from A$1,774,226 to A$2,077,845 a year, and the overflow costs A$470,286 a year in total.

## Assumptions you can change

Every shared assumption is one line in `src/mip_common.py`.

| Setting | Value | Meaning |
| --- | --- | --- |
| `PERIOD_DAYS` | 7 | Parcels are collected for a week, then leave together. |
| `DISTANCE_MULTIPLIER` | 1.20 | Straight-line distance times this gives modelled road distance. |
| `MAX_VOLUME_FILL` | 0.85 | A vehicle counts as full at 85% of its usable volume. |
| `MAX_DETOUR_RATIO` | 1.50 | A transfer route may be at most 50% longer than the direct route. |
| `N_PARCELS` | 900,000 | Yearly parcels used for the candidate facility question. |

## Limits of the model

- Weekly departures do not meet the delivery promises in the data, which are 2 to 5 days for interstate parcels. Departures every two days raise the optimised truck cost to A$4.37 million a year.
- Trips are one-way. Return journeys and fleet size are not modelled.
- Overload is a yearly count against yearly capacity. Queues and delays are not modelled.
- A candidate takes the same share of every lane in every week.
- Eight of the nine candidates have an estimated fixed cost of A$0.83 per parcel of yearly capacity, the same rate as the existing facilities. Only Melbourne West has lease, staffing and maintenance records.
- The candidate comparison prices trucks from every fourth week and scales the result to a year.

## How to run it

Install the packages. The optimisation needs `pulp` 4 or later and `highspy`.

```
python -m pip install -r requirements.txt
```

Run the scripts in this order from the project root:

```
python src/generate_data.py
python src/load_database.py
python src/mip_facility_registry.py
python src/mip_transport_costs.py
python src/mip_parcel_profiles.py
python src/mip_vehicle_registry.py
python src/mip_demand.py
python src/mip_truck_allocation.py
python src/mip_optimization.py
python src/mip_facility_opening.py
python src/mip_figures.py
```

Steps 32 and 33 take about 80 seconds and three minutes. `mip_figures.py` redraws the three charts in this README from the result tables, so they stay in step with the model.

## Files

| File | Steps | What it does |
| --- | --- | --- |
| `src/generate_data.py` | | Simulates parcels, facilities and tracking events as CSV files |
| `src/load_database.py` | | Loads the CSV files into `parcel_delivery.duckdb` |
| `src/build_dashboard.py` | | Four delivery-performance charts |
| `src/executive_dashboard.py` | | One-page executive dashboard |
| `src/mip_common.py` | | Shared paths, settings and input loading for the MIP steps |
| `src/mip_facility_registry.py` | 1 to 25 | Facilities, costs, scenarios and coordinates |
| `src/mip_transport_costs.py` | 26, 27 | Distances between facilities |
| `src/mip_parcel_profiles.py` | 28 | Volume and handling cost per parcel size |
| `src/mip_vehicle_registry.py` | 29 | Vehicle types, capacities and costs |
| `src/mip_demand.py` | 30 | Weekly demand per scenario, lane and size |
| `src/mip_truck_allocation.py` | 31 | Direct-shipping baseline |
| `src/mip_optimization.py` | 32 | Routing and fleet MILP |
| `src/mip_facility_opening.py` | 33 | Candidate facility choice |
| `src/mip_figures.py` | | Draws the three result charts into `figures/` |
| `src/mini_mip.py` | | Three-facility teaching example |

## Disclaimer

Synthetic data for a portfolio demonstration. Not actual Australia Post routes, prices, facilities or performance.