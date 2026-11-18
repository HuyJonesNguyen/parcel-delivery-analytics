"""Steps 1 to 25: facility registry, costs, scenarios and coordinates.

Run from the project root:
    python src/mip_facility_registry.py

Creates data/processed/mip_network.duckdb and registers:
    - facilities (eight Active, nine Candidate)
    - lease, staffing and maintenance cost records
    - optimisation scenarios
    - geographic references and coordinates

The historical parcel_delivery.duckdb database is only read.
All capacities, costs and coordinates are synthetic assumptions.

Next step:
    python src/mip_transport_costs.py
"""

import duckdb

from mip_common import MIP_DB_PATH, PROJECT_ROOT

# ============================================================
# STEP 1: LOCATE THE PROJECT AND DATABASE
# ============================================================

# The path is defined once in mip_common.py and shared by every
# MIP script. A separate database protects parcel_delivery.duckdb.
DB_PATH = MIP_DB_PATH

DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ============================================================
# STEP 2: INITIALISE DATABASE
# ============================================================
def initialise_database():
    """
    Create the MIP facility registry if it does not exist.

    Existing records are preserved when the script reruns.
    """

    with duckdb.connect(str(DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS facility_registry (

                facility_id VARCHAR PRIMARY KEY,

                facility_name VARCHAR NOT NULL,

                state VARCHAR NOT NULL,

                facility_type VARCHAR NOT NULL,

                annual_capacity INTEGER NOT NULL,

                annual_fixed_cost_aud DOUBLE NOT NULL,

                latitude DOUBLE,

                longitude DOUBLE,

                status VARCHAR NOT NULL DEFAULT 'Candidate',

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                CHECK (annual_capacity > 0),

                CHECK (annual_fixed_cost_aud >= 0),

                CHECK (
                    status IN (
                        'Candidate',
                        'Active',
                        'Inactive'
                    )
                )
            )
        """)

    print("Facility registry initialised successfully.")
    print(f"Database: {DB_PATH}")


# ============================================================
# STEP 4: CREATE A NEW FACILITY
# ============================================================

def create_facility(
    facility_id,
    facility_name,
    state,
    facility_type,
    annual_capacity,
    annual_fixed_cost_aud,
    latitude,
    longitude
):
    """
    Register a new candidate facility in DuckDB.

    A newly registered facility starts without any
    parcel-processing activity.
    """

    # --------------------------------------------------------
    # 4.1 VALIDATE INPUTS
    # --------------------------------------------------------

    valid_states = {
        "VIC", "NSW", "QLD", "SA",
        "WA", "TAS", "ACT", "NT"
    }

    valid_types = {
        "Major Hub",
        "Regional Hub",
        "Local Facility"
    }

    if not isinstance(facility_id, str) or not facility_id.strip():
        raise ValueError("Facility ID cannot be empty.")

    if not isinstance(facility_name, str) or not facility_name.strip():
        raise ValueError("Facility name cannot be empty.")

    state = state.strip().upper()

    if state not in valid_states:
        raise ValueError(f"Invalid state: {state}")

    if facility_type not in valid_types:
        raise ValueError(f"Invalid facility type: {facility_type}")

    if annual_capacity <= 0:
        raise ValueError("Annual capacity must be positive.")

    if annual_fixed_cost_aud < 0:
        raise ValueError("Fixed cost cannot be negative.")

    if latitude is not None and not -90 <= latitude <= 90:
        raise ValueError("Invalid latitude.")

    if longitude is not None and not -180 <= longitude <= 180:
        raise ValueError("Invalid longitude.")

    # --------------------------------------------------------
    # 4.2 INSERT INTO DATABASE
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        # Check for an existing facility ID.
        existing = con.execute("""
            SELECT COUNT(*)
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()[0]

        if existing > 0:
            print(f"Facility {facility_id} already exists.")
            return

        # Insert new facility metadata.
        con.execute("""
            INSERT INTO facility_registry (
                facility_id,
                facility_name,
                state,
                facility_type,
                annual_capacity,
                annual_fixed_cost_aud,
                latitude,
                longitude,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Candidate')
        """, [
            facility_id.strip(),
            facility_name.strip(),
            state,
            facility_type,
            annual_capacity,
            annual_fixed_cost_aud,
            latitude,
            longitude
        ])

    print(f"Facility {facility_id} registered successfully.")
    print("Status: Candidate")
    print("Processing records: 0")


# ============================================================
# STEP 6: INITIALISE FACILITY LEASES TABLE
# ============================================================

def initialise_lease_table():
    """
    Create the facility_leases table if it does not exist.
    Existing lease records are preserved.
    """

    with duckdb.connect(str(DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS facility_leases (

                lease_id VARCHAR PRIMARY KEY,

                facility_id VARCHAR NOT NULL,

                lease_start_date DATE NOT NULL,

                lease_term_months INTEGER NOT NULL,

                rent_amount_aud DOUBLE NOT NULL,

                payment_frequency VARCHAR NOT NULL,

                setup_cost_aud DOUBLE NOT NULL DEFAULT 0,

                annual_rent_increase_pct DOUBLE NOT NULL DEFAULT 0,

                renewal_option BOOLEAN NOT NULL DEFAULT FALSE,

                renewal_term_months INTEGER,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (facility_id)
                    REFERENCES facility_registry(facility_id),

                CHECK (lease_term_months > 0),

                CHECK (rent_amount_aud >= 0),

                CHECK (setup_cost_aud >= 0),

                CHECK (annual_rent_increase_pct >= 0),

                CHECK (
                    payment_frequency IN (
                        'Monthly',
                        'Quarterly',
                        'Annually'
                    )
                ),

                CHECK (
                    renewal_term_months IS NULL
                    OR renewal_term_months > 0
                )
            )
        """)

    print("Facility leases table initialised successfully.")


# ============================================================
# STEP 8: CREATE A FACILITY LEASE
# ============================================================

def create_lease(
    lease_id,
    facility_id,
    lease_start_date,
    lease_term_months,
    rent_amount_aud,
    payment_frequency,
    setup_cost_aud=0,
    annual_rent_increase_pct=0,
    renewal_option=False,
    renewal_term_months=None
):
    """
    Register a lease for an existing facility.

    Rental payments can be monthly, quarterly or annually.
    """

    # --------------------------------------------------------
    # 8.1 VALIDATE LEASE INFORMATION
    # --------------------------------------------------------

    if not isinstance(lease_id, str) or not lease_id.strip():
        raise ValueError("Lease ID cannot be empty.")

    if not isinstance(facility_id, str) or not facility_id.strip():
        raise ValueError("Facility ID cannot be empty.")

    lease_id = lease_id.strip()
    facility_id = facility_id.strip()

    payments_per_year = {
        "Monthly": 12,
        "Quarterly": 4,
        "Annually": 1
    }

    if not isinstance(payment_frequency, str):
        raise ValueError("Payment frequency must be text.")

    payment_frequency = payment_frequency.strip().title()

    if payment_frequency not in payments_per_year:
        raise ValueError("Use Monthly, Quarterly or Annually.")

    if lease_term_months <= 0:
        raise ValueError("Lease term must be positive.")

    if rent_amount_aud < 0:
        raise ValueError("Rent cannot be negative.")

    if setup_cost_aud < 0:
        raise ValueError("Setup cost cannot be negative.")

    if annual_rent_increase_pct < 0:
        raise ValueError("Rent increase cannot be negative.")

    if renewal_option:
        if renewal_term_months is None or renewal_term_months <= 0:
            raise ValueError(
                "A renewal term is required when renewal is available."
            )
    elif renewal_term_months is not None:
        raise ValueError(
            "Renewal term must be None if renewal is unavailable."
        )

    # --------------------------------------------------------
    # 8.2 NORMALISE RENTAL PAYMENTS
    # --------------------------------------------------------

    annual_rent_aud = (
        rent_amount_aud * payments_per_year[payment_frequency]
    )

    monthly_rent_aud = annual_rent_aud / 12

    # --------------------------------------------------------
    # 8.3 SAVE THE LEASE TO DUCKDB
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        facility_exists = con.execute("""
            SELECT COUNT(*)
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()[0]

        if facility_exists == 0:
            raise ValueError(
                f"Facility {facility_id} is not registered."
            )

        lease_exists = con.execute("""
            SELECT COUNT(*)
            FROM facility_leases
            WHERE lease_id = ?
        """, [lease_id]).fetchone()[0]

        if lease_exists > 0:
            print(f"Lease {lease_id} already exists.")
            return

        con.execute("""
            INSERT INTO facility_leases (
                lease_id,
                facility_id,
                lease_start_date,
                lease_term_months,
                rent_amount_aud,
                payment_frequency,
                setup_cost_aud,
                annual_rent_increase_pct,
                renewal_option,
                renewal_term_months
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            lease_id,
            facility_id,
            lease_start_date,
            lease_term_months,
            rent_amount_aud,
            payment_frequency,
            setup_cost_aud,
            annual_rent_increase_pct,
            renewal_option,
            renewal_term_months
        ])

    print(f"\nLease {lease_id} registered successfully.")
    print(f"Facility: {facility_id}")
    print(f"Payment frequency: {payment_frequency}")
    print(f"Rent per payment: AUD ${rent_amount_aud:,.2f}")
    print(f"Monthly equivalent: AUD ${monthly_rent_aud:,.2f}")
    print(f"Annual equivalent: AUD ${annual_rent_aud:,.2f}")
    print(f"Setup cost: AUD ${setup_cost_aud:,.2f}")
    print(f"Renewal available: {renewal_option}")


# ============================================================
# STEP 9: READ AND CALCULATE FACILITY LEASE COSTS
# ============================================================

def calculate_lease_costs(lease_id):
    """
    Retrieve a lease and calculate the rental cost
    for each year of its initial agreement.

    Renewal periods are not included automatically.
    """

    # --------------------------------------------------------
    # 9.1 READ THE LEASE FROM DUCKDB
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        lease = con.execute("""
            SELECT
                facility_id,
                lease_term_months,
                rent_amount_aud,
                payment_frequency,
                setup_cost_aud,
                annual_rent_increase_pct,
                renewal_option,
                renewal_term_months
            FROM facility_leases
            WHERE lease_id = ?
        """, [lease_id]).fetchone()

    if lease is None:
        raise ValueError(f"Lease {lease_id} does not exist.")

    (
        facility_id,
        lease_term_months,
        rent_amount_aud,
        payment_frequency,
        setup_cost_aud,
        annual_rent_increase_pct,
        renewal_option,
        renewal_term_months
    ) = lease

    # --------------------------------------------------------
    # 9.2 CONVERT RENT INTO ANNUAL COST
    # --------------------------------------------------------

    payments_per_year = {
        "Monthly": 12,
        "Quarterly": 4,
        "Annually": 1
    }

    annual_rent = (
        rent_amount_aud * payments_per_year[payment_frequency]
    )

    annual_increase = annual_rent_increase_pct / 100

    # --------------------------------------------------------
    # 9.3 CALCULATE LEASE COSTS BY YEAR
    # --------------------------------------------------------

    results = []
    remaining_months = lease_term_months
    year = 1

    while remaining_months > 0:

        months_in_year = min(12, remaining_months)

        rent_for_full_year = (
            annual_rent * (1 + annual_increase) ** (year - 1)
        )

        rent_for_period = (
            rent_for_full_year * months_in_year / 12
        )

        setup_cost = setup_cost_aud if year == 1 else 0

        results.append({
            "year": year,
            "months": months_in_year,
            "rent_aud": round(rent_for_period, 2),
            "setup_cost_aud": round(setup_cost, 2),
            "total_cost_aud": round(
                rent_for_period + setup_cost, 2
            )
        })

        remaining_months -= months_in_year
        year += 1

    # --------------------------------------------------------
    # 9.4 DISPLAY RESULTS
    # --------------------------------------------------------

    print(f"\nLEASE COST FORECAST: {lease_id}")
    print(f"Facility: {facility_id}")
    print(f"Initial lease term: {lease_term_months} months")
    print("-" * 65)

    for row in results:
        print(
            f"Year {row['year']}: "
            f"Rent A${row['rent_aud']:,.2f} | "
            f"Setup A${row['setup_cost_aud']:,.2f} | "
            f"Total A${row['total_cost_aud']:,.2f}"
        )

    total_cost = round(
        sum(row["total_cost_aud"] for row in results), 2
    )

    print("-" * 65)
    print(f"Total projected lease cost: A${total_cost:,.2f}")
    print(f"Renewal option available: {renewal_option}")

    if renewal_option:
        print(f"Optional renewal term: {renewal_term_months} months")

    return results

# ============================================================
# STEP 10: INITIALISE FACILITY STAFFING TABLE
# ============================================================

def initialise_staffing_table():
    """
    Create the staffing requirements table.

    Each record describes a role and its minimum
    staffing requirement for a facility.

    Existing records are preserved.
    """

    with duckdb.connect(str(DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS facility_staffing (

                staffing_id VARCHAR PRIMARY KEY,

                facility_id VARCHAR NOT NULL,

                role_name VARCHAR NOT NULL,

                employment_type VARCHAR NOT NULL,

                minimum_headcount INTEGER NOT NULL,

                annual_salary_per_employee_aud DOUBLE NOT NULL,

                employer_oncost_pct DOUBLE NOT NULL DEFAULT 0,

                shift_name VARCHAR NOT NULL,

                operating_days_per_week INTEGER NOT NULL,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (facility_id)
                    REFERENCES facility_registry(facility_id),

                CHECK (minimum_headcount > 0),

                CHECK (annual_salary_per_employee_aud >= 0),

                CHECK (employer_oncost_pct >= 0),

                CHECK (
                    operating_days_per_week
                    BETWEEN 1 AND 7
                ),

                CHECK (
                    employment_type IN (
                        'Full-time',
                        'Part-time',
                        'Casual',
                        'Contractor'
                    )
                ),

                CHECK (
                    shift_name IN (
                        'Day',
                        'Evening',
                        'Night',
                        'Flexible'
                    )
                )
            )
        """)

    print("Facility staffing table initialised successfully.")

# ============================================================
# STEP 11: CREATE A FACILITY STAFFING REQUIREMENT
# ============================================================

def create_staffing_requirement(
    staffing_id,
    facility_id,
    role_name,
    employment_type,
    minimum_headcount,
    annual_salary_per_employee_aud,
    employer_oncost_pct,
    shift_name,
    operating_days_per_week
):
    """
    Register a staffing requirement for an existing facility.

    Each record describes one role and shift assignment.
    """

    # --------------------------------------------------------
    # 11.1 VALIDATE INPUTS
    # --------------------------------------------------------

    valid_employment_types = {
        "Full-time", "Part-time", "Casual", "Contractor"
    }

    valid_shifts = {
        "Day", "Evening", "Night", "Flexible"
    }

    if not isinstance(staffing_id, str) or not staffing_id.strip():
        raise ValueError("Staffing ID cannot be empty.")

    if not isinstance(facility_id, str) or not facility_id.strip():
        raise ValueError("Facility ID cannot be empty.")

    if not isinstance(role_name, str) or not role_name.strip():
        raise ValueError("Role name cannot be empty.")

    staffing_id = staffing_id.strip()
    facility_id = facility_id.strip()
    role_name = role_name.strip()

    if employment_type not in valid_employment_types:
        raise ValueError("Invalid employment type.")

    if shift_name not in valid_shifts:
        raise ValueError("Invalid shift name.")

    if not isinstance(minimum_headcount, int) or minimum_headcount <= 0:
        raise ValueError("Minimum headcount must be a positive integer.")

    if annual_salary_per_employee_aud < 0:
        raise ValueError("Annual salary cannot be negative.")

    if employer_oncost_pct < 0:
        raise ValueError("Employer on-cost percentage cannot be negative.")

    if not isinstance(operating_days_per_week, int):
        raise ValueError("Operating days must be an integer.")

    if not 1 <= operating_days_per_week <= 7:
        raise ValueError("Operating days must be between 1 and 7.")

    # --------------------------------------------------------
    # 11.2 VERIFY FACILITY AND CHECK FOR DUPLICATES
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        facility_exists = con.execute("""
            SELECT COUNT(*)
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()[0]

        if facility_exists == 0:
            raise ValueError(
                f"Facility {facility_id} does not exist."
            )

        staffing_exists = con.execute("""
            SELECT COUNT(*)
            FROM facility_staffing
            WHERE staffing_id = ?
        """, [staffing_id]).fetchone()[0]

        if staffing_exists > 0:
            print(f"Staffing requirement {staffing_id} already exists.")
            return

        # ----------------------------------------------------
        # 11.3 INSERT STAFFING REQUIREMENT
        # ----------------------------------------------------

        con.execute("""
            INSERT INTO facility_staffing (
                staffing_id,
                facility_id,
                role_name,
                employment_type,
                minimum_headcount,
                annual_salary_per_employee_aud,
                employer_oncost_pct,
                shift_name,
                operating_days_per_week
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            staffing_id,
            facility_id,
            role_name,
            employment_type,
            minimum_headcount,
            annual_salary_per_employee_aud,
            employer_oncost_pct,
            shift_name,
            operating_days_per_week
        ])

    # --------------------------------------------------------
    # 11.4 CALCULATE ESTIMATED ANNUAL LABOUR COST
    # --------------------------------------------------------

    annual_labour_cost = (
        minimum_headcount
        * annual_salary_per_employee_aud
        * (1 + employer_oncost_pct / 100)
    )

    print(f"\nStaffing requirement {staffing_id} registered.")
    print(f"Facility: {facility_id}")
    print(f"Role: {role_name}")
    print(f"Headcount: {minimum_headcount}")
    print(f"Shift: {shift_name}")
    print(f"Estimated annual labour cost: A${annual_labour_cost:,.2f}")

# ============================================================
# STEP 12: READ AND CALCULATE FACILITY STAFFING COSTS
# ============================================================

def calculate_staffing_costs(facility_id):
    """
    Retrieve staffing requirements for one facility
    and calculate its estimated annual labour costs.

    Returns a dictionary containing the role-level
    costs and facility-level totals.
    """

    # --------------------------------------------------------
    # 12.1 VERIFY FACILITY AND RETRIEVE STAFFING RECORDS
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        facility = con.execute("""
            SELECT facility_name, status
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()

        if facility is None:
            raise ValueError(
                f"Facility {facility_id} does not exist."
            )

        staffing_records = con.execute("""
            SELECT
                staffing_id,
                role_name,
                employment_type,
                minimum_headcount,
                annual_salary_per_employee_aud,
                employer_oncost_pct,
                shift_name,
                operating_days_per_week
            FROM facility_staffing
            WHERE facility_id = ?
            ORDER BY role_name, shift_name
        """, [facility_id]).fetchall()

    # --------------------------------------------------------
    # 12.2 CALCULATE COSTS FOR EACH STAFFING REQUIREMENT
    # --------------------------------------------------------

    role_costs = []
    total_headcount = 0
    total_base_salary = 0
    total_employer_oncosts = 0

    for record in staffing_records:
        (
            staffing_id,
            role_name,
            employment_type,
            minimum_headcount,
            annual_salary,
            employer_oncost_pct,
            shift_name,
            operating_days_per_week
        ) = record

        base_salary_cost = minimum_headcount * annual_salary

        employer_oncost = (
                base_salary_cost * employer_oncost_pct / 100
        )

        annual_labour_cost = (
                base_salary_cost + employer_oncost
        )

        role_costs.append({
            "staffing_id": staffing_id,
            "role_name": role_name,
            "employment_type": employment_type,
            "headcount": minimum_headcount,
            "shift": shift_name,
            "operating_days_per_week": operating_days_per_week,
            "base_salary_aud": round(base_salary_cost, 2),
            "employer_oncost_aud": round(employer_oncost, 2),
            "annual_labour_cost_aud": round(annual_labour_cost, 2)
        })

        total_headcount += minimum_headcount
        total_base_salary += base_salary_cost
        total_employer_oncosts += employer_oncost

    total_annual_labour_cost = (
            total_base_salary + total_employer_oncosts
    )

    # --------------------------------------------------------
    # 12.3 DISPLAY RESULTS
    # --------------------------------------------------------

    facility_name, facility_status = facility

    print(f"\nSTAFFING COST SUMMARY: {facility_id}")
    print(f"Facility: {facility_name}")
    print(f"Status: {facility_status}")
    print("-" * 65)

    if not role_costs:
        print("No staffing requirements registered.")

    for role in role_costs:
        print(
            f"{role['role_name']} | "
            f"Headcount: {role['headcount']} | "
            f"Annual cost: A${role['annual_labour_cost_aud']:,.2f}"
        )

    print("-" * 65)
    print(f"Total required headcount: {total_headcount}")
    print(f"Total base salaries: A${total_base_salary:,.2f}")
    print(f"Total employer on-costs: A${total_employer_oncosts:,.2f}")
    print(f"Total annual labour cost: A${total_annual_labour_cost:,.2f}")

    # --------------------------------------------------------
    # 12.4 RETURN RESULTS FOR FUTURE MIP USE
    # --------------------------------------------------------

    return {
        "facility_id": facility_id,
        "facility_status": facility_status,
        "role_costs": role_costs,
        "total_headcount": total_headcount,
        "total_base_salary_aud": round(total_base_salary, 2),
        "total_employer_oncosts_aud": round(total_employer_oncosts, 2),
        "total_annual_labour_cost_aud": round(
            total_annual_labour_cost, 2
        )
    }


# ============================================================
# STEP 13: INITIALISE FACILITY MAINTENANCE TABLE
# ============================================================

def initialise_maintenance_table():
    """
    Create a table for facility maintenance costs.

    Store maintenance expense assumptions separately
    from employee salaries.

    Existing maintenance records are preserved.
    """

    with duckdb.connect(str(DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS facility_maintenance (

                maintenance_id VARCHAR PRIMARY KEY,

                facility_id VARCHAR NOT NULL,

                maintenance_category VARCHAR NOT NULL,

                expense_description VARCHAR NOT NULL,

                cost_amount_aud DOUBLE NOT NULL,

                payment_frequency VARCHAR NOT NULL,

                cost_type VARCHAR NOT NULL,

                annual_cost_increase_pct DOUBLE
                    NOT NULL DEFAULT 0,

                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (facility_id)
                    REFERENCES facility_registry(facility_id),

                CHECK (cost_amount_aud >= 0),

                CHECK (annual_cost_increase_pct >= 0),

                CHECK (
                    maintenance_category IN (
                        'Preventive',
                        'Corrective',
                        'Inspection',
                        'Spare Parts',
                        'External Contract'
                    )
                ),

                CHECK (
                    payment_frequency IN (
                        'Monthly',
                        'Quarterly',
                        'Annually',
                        'One-time'
                    )
                ),

                CHECK (
                    cost_type IN (
                        'Fixed',
                        'Variable'
                    )
                )
            )
        """)

    print("Facility maintenance table initialised successfully.")



# ============================================================
# STEP 14: CREATE A FACILITY MAINTENANCE EXPENSE
# ============================================================

def create_maintenance_expense(
    maintenance_id,
    facility_id,
    maintenance_category,
    expense_description,
    cost_amount_aud,
    payment_frequency,
    cost_type,
    annual_cost_increase_pct=0
):
    """
    Register a fixed recurring or one-time maintenance expense
    for an existing facility.

    Variable maintenance costs will be modelled separately.
    """

    # --------------------------------------------------------
    # 14.1 VALIDATE INPUTS
    # --------------------------------------------------------

    # Check required identifiers and description.
    if not isinstance(maintenance_id, str) or not maintenance_id.strip():
        raise ValueError("Maintenance ID cannot be empty.")

    if not isinstance(facility_id, str) or not facility_id.strip():
        raise ValueError("Facility ID cannot be empty.")

    if not isinstance(expense_description, str) or not expense_description.strip():
        raise ValueError("Expense description cannot be empty.")

    # Remove surrounding spaces from text inputs.
    maintenance_id = maintenance_id.strip()
    facility_id = facility_id.strip()
    expense_description = expense_description.strip()

    # Allowed maintenance categories and payment frequencies.
    valid_categories = {
        "Preventive",
        "Corrective",
        "Inspection",
        "Spare Parts",
        "External Contract"
    }

    valid_frequencies = {
        "Monthly",
        "Quarterly",
        "Annually",
        "One-time"
    }

    if maintenance_category not in valid_categories:
        raise ValueError("Invalid maintenance category.")

    if payment_frequency not in valid_frequencies:
        raise ValueError("Invalid payment frequency.")

    # Step 14 supports fixed maintenance costs only.
    if cost_type != "Fixed":
        raise ValueError("Only Fixed costs are supported in Step 14.")

    # Prevent invalid monetary amounts.
    if not isinstance(cost_amount_aud, (int, float)) or cost_amount_aud < 0:
        raise ValueError("Maintenance cost must be non-negative.")

    if (
        not isinstance(annual_cost_increase_pct, (int, float))
        or annual_cost_increase_pct < 0
    ):
        raise ValueError("Annual cost increase must be non-negative.")

    # --------------------------------------------------------
    # 14.2 VERIFY FACILITY AND PREVENT DUPLICATES
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        # Check whether the facility exists.
        facility_exists = con.execute("""
            SELECT COUNT(*)
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()[0]

        if facility_exists == 0:
            raise ValueError(f"Facility {facility_id} does not exist.")

        # Check whether the maintenance ID is already used.
        maintenance_exists = con.execute("""
            SELECT COUNT(*)
            FROM facility_maintenance
            WHERE maintenance_id = ?
        """, [maintenance_id]).fetchone()[0]

        if maintenance_exists > 0:
            print(f"Maintenance expense {maintenance_id} already exists.")
            return

        # ----------------------------------------------------
        # 14.3 INSERT MAINTENANCE EXPENSE
        # ----------------------------------------------------

        # Save the maintenance expense in DuckDB.
        con.execute("""
            INSERT INTO facility_maintenance (
                maintenance_id,
                facility_id,
                maintenance_category,
                expense_description,
                cost_amount_aud,
                payment_frequency,
                cost_type,
                annual_cost_increase_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            maintenance_id,
            facility_id,
            maintenance_category,
            expense_description,
            cost_amount_aud,
            payment_frequency,
            cost_type,
            annual_cost_increase_pct
        ])

    # --------------------------------------------------------
    # 14.4 DISPLAY REGISTRATION RESULT
    # --------------------------------------------------------

    print(f"\nMaintenance expense {maintenance_id} registered.")
    print(f"Facility: {facility_id}")
    print(f"Category: {maintenance_category}")
    print(f"Description: {expense_description}")
    print(f"Payment frequency: {payment_frequency}")
    print(f"Cost per payment: A${cost_amount_aud:,.2f}")


# ============================================================
# STEP 15: READ AND CALCULATE FACILITY MAINTENANCE COSTS
# ============================================================

def calculate_maintenance_costs(facility_id):
    """
    Retrieve maintenance expenses for an existing facility.

    Calculate:
        1. Annual recurring maintenance costs
        2. One-time maintenance expenses
        3. First-year maintenance budget

    Only fixed maintenance expenses are included.
    """

    # --------------------------------------------------------
    # 15.1 VERIFY FACILITY AND RETRIEVE MAINTENANCE RECORDS
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        # Verify that the requested facility exists.
        facility = con.execute("""
            SELECT facility_name, status
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()

        if facility is None:
            raise ValueError(
                f"Facility {facility_id} does not exist."
            )

        # Retrieve the registered fixed maintenance expenses.
        maintenance_records = con.execute("""
            SELECT
                maintenance_id,
                maintenance_category,
                expense_description,
                cost_amount_aud,
                payment_frequency,
                cost_type,
                annual_cost_increase_pct
            FROM facility_maintenance
            WHERE facility_id = ?
              AND cost_type = 'Fixed'
            ORDER BY maintenance_id
        """, [facility_id]).fetchall()

    # --------------------------------------------------------
    # 15.2 CONVERT PAYMENT FREQUENCIES INTO ANNUAL COSTS
    # --------------------------------------------------------

    # Number of payments made in one year.
    payments_per_year = {
        "Monthly": 12,
        "Quarterly": 4,
        "Annually": 1
    }

    expense_details = []

    total_recurring_cost = 0
    total_one_time_cost = 0

    for record in maintenance_records:

        (
            maintenance_id,
            category,
            description,
            amount,
            frequency,
            cost_type,
            annual_increase_pct
        ) = record

        # One-time expenses are not repeated every year.
        if frequency == "One-time":

            annual_cost = 0
            one_time_cost = amount

        else:

            # Convert recurring payments into annual equivalents.
            annual_cost = amount * payments_per_year[frequency]
            one_time_cost = 0

        total_recurring_cost += annual_cost
        total_one_time_cost += one_time_cost

        # Keep detailed costs for future MIP calculations.
        expense_details.append({
            "maintenance_id": maintenance_id,
            "category": category,
            "description": description,
            "payment_frequency": frequency,
            "annual_cost_increase_pct": annual_increase_pct,
            "annual_recurring_cost_aud": round(annual_cost, 2),
            "one_time_cost_aud": round(one_time_cost, 2)
        })

    # --------------------------------------------------------
    # 15.3 CALCULATE FIRST-YEAR MAINTENANCE BUDGET
    # --------------------------------------------------------

    first_year_maintenance_cost = (
        total_recurring_cost + total_one_time_cost
    )

    # --------------------------------------------------------
    # 15.4 DISPLAY AND RETURN RESULTS
    # --------------------------------------------------------

    facility_name, facility_status = facility

    print(f"\nMAINTENANCE COST SUMMARY: {facility_id}")
    print(f"Facility: {facility_name}")
    print(f"Status: {facility_status}")
    print("-" * 65)

    for expense in expense_details:

        print(
            f"{expense['description']} | "
            f"Annual recurring: "
            f"A${expense['annual_recurring_cost_aud']:,.2f} | "
            f"One-time: "
            f"A${expense['one_time_cost_aud']:,.2f}"
        )

    print("-" * 65)

    print(
        f"Annual recurring maintenance: "
        f"A${total_recurring_cost:,.2f}"
    )

    print(
        f"One-time maintenance expenses: "
        f"A${total_one_time_cost:,.2f}"
    )

    print(
        f"First-year maintenance budget: "
        f"A${first_year_maintenance_cost:,.2f}"
    )

    # Return structured results for the future MIP model.
    return {
        "facility_id": facility_id,
        "facility_status": facility_status,
        "expense_details": expense_details,
        "annual_recurring_maintenance_aud": round(
            total_recurring_cost, 2
        ),
        "one_time_maintenance_aud": round(
            total_one_time_cost, 2
        ),
        "first_year_maintenance_aud": round(
            first_year_maintenance_cost, 2
        )
    }

# ============================================================
# STEP 16: CALCULATE TOTAL FACILITY COSTS
# ============================================================

def calculate_facility_costs(facility_id):
    """
    Combine lease, staffing and maintenance costs
    for one registered facility.

    Returns first-year cost estimates for future MIP use.

    This function does not change facility status.
    """

    # --------------------------------------------------------
    # 16.1 RETRIEVE FACILITY INFORMATION
    # --------------------------------------------------------

    # Read basic information from the facility registry.
    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        facility = con.execute("""
            SELECT
                facility_name,
                status,
                annual_capacity
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()

        # Retrieve all registered lease agreements.
        leases = con.execute("""
            SELECT
                lease_id,
                rent_amount_aud,
                payment_frequency,
                setup_cost_aud
            FROM facility_leases
            WHERE facility_id = ?
        """, [facility_id]).fetchall()

    if facility is None:
        raise ValueError(
            f"Facility {facility_id} does not exist."
        )

    facility_name, facility_status, annual_capacity = facility

    # Require exactly one lease for this first-year cost model.
    # We will support lease history and date selection later.
    if len(leases) != 1:
        raise ValueError(
            f"Expected one lease for {facility_id}, "
            f"but found {len(leases)}."
        )

    lease_id, rent_amount, frequency, setup_cost = leases[0]

    # --------------------------------------------------------
    # 16.2 CALCULATE LEASE COSTS
    # --------------------------------------------------------

    # Convert rental frequency to an annual equivalent.
    payments_per_year = {
        "Monthly": 12,
        "Quarterly": 4,
        "Annually": 1
    }

    annual_rent = (
        rent_amount * payments_per_year[frequency]
    )

    # Setup costs are incurred only when opening the facility.
    one_time_setup_cost = setup_cost

    # --------------------------------------------------------
    # 16.3 RETRIEVE STAFFING AND MAINTENANCE COSTS
    # --------------------------------------------------------

    # Use the existing Step 12 function.
    staffing_summary = calculate_staffing_costs(
        facility_id=facility_id
    )

    if staffing_summary is None:
        raise ValueError(
            f"No staffing requirements found for {facility_id}."
        )

    annual_staffing_cost = (
        staffing_summary["total_annual_labour_cost_aud"]
    )

    # Use the existing Step 15 function.
    maintenance_summary = calculate_maintenance_costs(
        facility_id=facility_id
    )

    annual_maintenance_cost = (
        maintenance_summary["annual_recurring_maintenance_aud"]
    )

    one_time_maintenance_cost = (
        maintenance_summary["one_time_maintenance_aud"]
    )

    # Calculate total recurring annual costs.
    annual_recurring_cost = (
        annual_rent
        + annual_staffing_cost
        + annual_maintenance_cost
    )

    # Calculate expenses incurred only once.
    total_one_time_cost = (
        one_time_setup_cost
        + one_time_maintenance_cost
    )

    # Calculate the first-year facility cost.
    first_year_total_cost = (
        annual_recurring_cost
        + total_one_time_cost
    )

    # --------------------------------------------------------
    # 16.4 DISPLAY AND RETURN RESULTS
    # --------------------------------------------------------

    print(f"\nFACILITY COST SUMMARY: {facility_id}")
    print(f"Facility: {facility_name}")
    print(f"Status: {facility_status}")
    print(f"Annual capacity: {annual_capacity:,} parcels")
    print("-" * 65)

    print(f"Annual rent: A${annual_rent:,.2f}")
    print(f"Annual staffing: A${annual_staffing_cost:,.2f}")
    print(f"Annual maintenance: A${annual_maintenance_cost:,.2f}")

    print("-" * 65)

    print(f"Annual recurring costs: A${annual_recurring_cost:,.2f}")
    print(f"One-time opening costs: A${total_one_time_cost:,.2f}")
    print(f"First-year total cost: A${first_year_total_cost:,.2f}")

    # Return the cost information for future MIP optimisation.
    return {
        "facility_id": facility_id,
        "facility_name": facility_name,
        "facility_status": facility_status,
        "annual_capacity": annual_capacity,
        "lease_id": lease_id,
        "annual_rent_aud": round(annual_rent, 2),
        "annual_staffing_aud": round(annual_staffing_cost, 2),
        "annual_maintenance_aud": round(annual_maintenance_cost, 2),
        "annual_recurring_cost_aud": round(annual_recurring_cost, 2),
        "one_time_opening_cost_aud": round(total_one_time_cost, 2),
        "first_year_total_cost_aud": round(first_year_total_cost, 2)
    }

# ============================================================
# STEP 16B: KEEP ONE FIXED-COST FIGURE PER FACILITY
# ============================================================

def sync_facility_fixed_costs():
    """
    Copy each facility's calculated recurring cost into
    facility_registry.annual_fixed_cost_aud.

    Why: the registry holds a fixed cost typed in by hand, while
    Step 16 calculates one from the lease, staffing and maintenance
    records. A facility-opening model must read ONE figure, so the
    calculated figure replaces the typed one wherever the detailed
    records exist (exactly one lease and at least one staffing row).

    Candidates whose cost is still 0 are listed as "not yet
    estimated". A solver would treat 0 as free, so a future
    facility-opening model must exclude those candidates.

    Safe to rerun: a second run finds nothing to change.
    """

    # --------------------------------------------------------
    # 16B.1 FIND FACILITIES WITH COMPLETE COST RECORDS
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        facilities = con.execute("""
            SELECT
                f.facility_id,
                f.status,
                f.annual_fixed_cost_aud,
                (SELECT COUNT(*) FROM facility_leases AS l
                 WHERE l.facility_id = f.facility_id) AS lease_count,
                (SELECT COUNT(*) FROM facility_staffing AS s
                 WHERE s.facility_id = f.facility_id) AS staffing_count
            FROM facility_registry AS f
            ORDER BY f.facility_id
        """).fetchall()

    # --------------------------------------------------------
    # 16B.2 CALCULATE AND COMPARE
    # --------------------------------------------------------

    updates = []
    not_estimated = []

    for facility_id, status, stored_cost, leases, staffing in facilities:

        has_cost_records = leases == 1 and staffing >= 1

        if not has_cost_records:
            if status == "Candidate" and stored_cost == 0:
                not_estimated.append(facility_id)
            continue

        calculated = calculate_facility_costs(facility_id)
        calculated_cost = calculated["annual_recurring_cost_aud"]

        if abs(calculated_cost - stored_cost) > 0.005:
            updates.append((facility_id, stored_cost, calculated_cost))

    # --------------------------------------------------------
    # 16B.3 SAVE THE CALCULATED FIGURES
    # --------------------------------------------------------

    if updates:
        with duckdb.connect(str(DB_PATH)) as con:
            for facility_id, _, calculated_cost in updates:
                con.execute("""
                    UPDATE facility_registry
                    SET annual_fixed_cost_aud = ?
                    WHERE facility_id = ?
                """, [calculated_cost, facility_id])

    # --------------------------------------------------------
    # 16B.4 DISPLAY AND RETURN RESULTS
    # --------------------------------------------------------

    print("\nFACILITY FIXED-COST CHECK")
    print("-" * 65)

    for facility_id, stored_cost, calculated_cost in updates:
        print(
            f"{facility_id}: registry A${stored_cost:,.2f} "
            f"replaced by calculated A${calculated_cost:,.2f}"
        )

    if not updates:
        print("Registry already matches the calculated costs.")

    if not_estimated:
        print(
            "Candidates with no cost estimate yet (stored as 0): "
            + ", ".join(not_estimated)
        )
        print(
            "Exclude these from any facility-opening model "
            "until their costs are registered."
        )

    return {
        "updated_facilities": [row[0] for row in updates],
        "candidates_without_cost": not_estimated
    }


# ============================================================
# STEP 17: INITIALISE MIP SCENARIOS TABLE
# ============================================================

def initialise_scenarios_table():
    """
    Create a table to store MIP optimisation scenarios.

    Each scenario defines the planning horizon,
    demand assumptions and facility selection policy.

    Existing scenarios are preserved.
    """

    with duckdb.connect(str(DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_scenarios (

                scenario_id VARCHAR PRIMARY KEY,

                scenario_name VARCHAR NOT NULL,

                description VARCHAR,

                planning_year INTEGER NOT NULL,

                demand_multiplier DOUBLE NOT NULL DEFAULT 1.0,

                allow_candidate_facilities BOOLEAN
                    NOT NULL DEFAULT TRUE,

                optimisation_objective VARCHAR
                    NOT NULL DEFAULT 'Minimise Total Cost',

                scenario_status VARCHAR
                    NOT NULL DEFAULT 'Draft',

                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,

                CHECK (planning_year >= 2025),

                CHECK (demand_multiplier > 0),

                CHECK (
                    optimisation_objective IN (
                        'Minimise Total Cost'
                    )
                ),

                CHECK (
                    scenario_status IN (
                        'Draft',
                        'Ready',
                        'Completed'
                    )
                )
            )
        """)

    print("MIP scenarios table initialised successfully.")

# ============================================================
# STEP 18: CREATE A MIP OPTIMISATION SCENARIO
# ============================================================

def create_scenario(
    scenario_id,
    scenario_name,
    description,
    planning_year,
    demand_multiplier=1.0,
    allow_candidate_facilities=True,
    optimisation_objective="Minimise Total Cost",
    scenario_status="Draft"
):
    """
    Register an optimisation scenario in DuckDB.

    Each scenario defines a planning year, demand adjustment
    and whether candidate facilities can be selected.

    Creating a scenario does not run the MIP optimiser.
    """

    # --------------------------------------------------------
    # 18.1 VALIDATE SCENARIO INFORMATION
    # --------------------------------------------------------

    # Validate the unique scenario ID.
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ValueError("Scenario ID cannot be empty.")

    # Validate the scenario name.
    if not isinstance(scenario_name, str) or not scenario_name.strip():
        raise ValueError("Scenario name cannot be empty.")

    # Remove unnecessary spaces.
    scenario_id = scenario_id.strip()
    scenario_name = scenario_name.strip()

    # Validate the optional description.
    if description is not None and not isinstance(description, str):
        raise ValueError("Description must be text or None.")

    # Planning year must meet our database constraint.
    if (
        not isinstance(planning_year, int)
        or isinstance(planning_year, bool)
        or planning_year < 2025
    ):
        raise ValueError("Planning year must be an integer >= 2025.")

    # Demand must be greater than zero.
    if (
        not isinstance(demand_multiplier, (int, float))
        or isinstance(demand_multiplier, bool)
        or not 0 < demand_multiplier < float("inf")
    ):
        raise ValueError("Demand multiplier must be positive and finite.")

    # Candidate eligibility must be a Boolean.
    if not isinstance(allow_candidate_facilities, bool):
        raise ValueError(
            "allow_candidate_facilities must be True or False."
        )

    # Our first MIP version supports one optimisation objective.
    valid_objectives = {
        "Minimise Total Cost"
    }

    if optimisation_objective not in valid_objectives:
        raise ValueError("Invalid optimisation objective.")

    # Validate scenario lifecycle status.
    valid_statuses = {
        "Draft",
        "Ready",
        "Completed"
    }

    if scenario_status not in valid_statuses:
        raise ValueError("Invalid scenario status.")

    # --------------------------------------------------------
    # 18.2 CHECK FOR AN EXISTING SCENARIO
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        existing = con.execute("""
            SELECT COUNT(*)
            FROM mip_scenarios
            WHERE scenario_id = ?
        """, [scenario_id]).fetchone()[0]

        # Skip registration when the scenario already exists.
        if existing > 0:
            print(f"Scenario {scenario_id} already exists.")
            return

        # ----------------------------------------------------
        # 18.3 INSERT SCENARIO INTO DUCKDB
        # ----------------------------------------------------

        con.execute("""
            INSERT INTO mip_scenarios (
                scenario_id,
                scenario_name,
                description,
                planning_year,
                demand_multiplier,
                allow_candidate_facilities,
                optimisation_objective,
                scenario_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            scenario_id,
            scenario_name,
            description,
            planning_year,
            demand_multiplier,
            allow_candidate_facilities,
            optimisation_objective,
            scenario_status
        ])

    # --------------------------------------------------------
    # 18.4 DISPLAY REGISTRATION RESULT
    # --------------------------------------------------------

    print(f"\nScenario {scenario_id} registered successfully.")
    print(f"Name: {scenario_name}")
    print(f"Planning year: {planning_year}")
    print(f"Demand multiplier: {demand_multiplier:.2f}")
    print(f"Candidate facilities allowed: {allow_candidate_facilities}")
    print(f"Objective: {optimisation_objective}")
    print(f"Status: {scenario_status}")

# ============================================================
# STEP 19: READ SCENARIOS AND IDENTIFY ELIGIBLE FACILITIES
# ============================================================

def get_scenario_facilities(scenario_id):
    """
    Retrieve scenario assumptions and eligible facilities.

    Eligibility rules:
        Active facilities: always eligible
        Candidate facilities: eligible if scenario allows
        Inactive facilities: excluded

    This function does not select or activate facilities.
    """

    # --------------------------------------------------------
    # 19.1 RETRIEVE SCENARIO CONFIGURATION
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        # Read the registered scenario.
        scenario = con.execute("""
            SELECT
                scenario_name,
                planning_year,
                demand_multiplier,
                allow_candidate_facilities,
                optimisation_objective,
                scenario_status
            FROM mip_scenarios
            WHERE scenario_id = ?
        """, [scenario_id]).fetchone()

        # Stop if the scenario does not exist.
        if scenario is None:
            raise ValueError(
                f"Scenario {scenario_id} does not exist."
            )

        (
            scenario_name,
            planning_year,
            demand_multiplier,
            allow_candidates,
            objective,
            scenario_status
        ) = scenario

        # ----------------------------------------------------
        # 19.2 IDENTIFY ELIGIBLE FACILITIES
        # ----------------------------------------------------

        # Active facilities are always eligible.
        # Candidate facilities depend on the scenario.
        if allow_candidates:

            facilities = con.execute("""
                SELECT
                    facility_id,
                    facility_name,
                    state,
                    facility_type,
                    annual_capacity,
                    status
                FROM facility_registry
                WHERE status IN ('Active', 'Candidate')
                ORDER BY state, facility_id
            """).fetchall()

        else:

            facilities = con.execute("""
                SELECT
                    facility_id,
                    facility_name,
                    state,
                    facility_type,
                    annual_capacity,
                    status
                FROM facility_registry
                WHERE status = 'Active'
                ORDER BY state, facility_id
            """).fetchall()

    # --------------------------------------------------------
    # 19.3 CONVERT FACILITY RECORDS TO DICTIONARIES
    # --------------------------------------------------------

    eligible_facilities = []

    for facility in facilities:

        (
            facility_id,
            facility_name,
            state,
            facility_type,
            annual_capacity,
            facility_status
        ) = facility

        eligible_facilities.append({
            "facility_id": facility_id,
            "facility_name": facility_name,
            "state": state,
            "facility_type": facility_type,
            "annual_capacity": annual_capacity,
            "status": facility_status
        })

    # --------------------------------------------------------
    # 19.4 DISPLAY AND RETURN RESULTS
    # --------------------------------------------------------

    print(f"\nSCENARIO: {scenario_id}")
    print(f"Name: {scenario_name}")
    print(f"Planning year: {planning_year}")
    print(f"Demand multiplier: {demand_multiplier:.2f}")
    print(f"Candidate facilities allowed: {allow_candidates}")
    print(f"Scenario status: {scenario_status}")
    print("-" * 65)

    if not eligible_facilities:
        print("No eligible facilities found.")

    for facility in eligible_facilities:
        print(
            f"{facility['facility_id']} | "
            f"{facility['facility_name']} | "
            f"{facility['status']} | "
            f"Capacity: {facility['annual_capacity']:,}"
        )

    print("-" * 65)
    print(f"Total eligible facilities: {len(eligible_facilities)}")

    # Return inputs for future optimisation.
    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario_name,
        "planning_year": planning_year,
        "demand_multiplier": demand_multiplier,
        "allow_candidate_facilities": allow_candidates,
        "optimisation_objective": objective,
        "scenario_status": scenario_status,
        "eligible_facilities": eligible_facilities
    }


# ============================================================
# STEP 20B: REGISTER PUBLIC GEOGRAPHIC REFERENCES FOR MIP
# ============================================================

def initialise_location_references():
    """
    Store publicly documented Australia Post locations
    as geographic references for the synthetic MIP model.

    These are NOT actual operational records, cost data,
    processing capacities or verified network assignments.
    """

    # --------------------------------------------------------
    # 20.1 DEFINE REFERENCE LOCATIONS
    # --------------------------------------------------------

    reference_locations = [
        ("VIC", "Melbourne Parcel Facility", "Sunshine West",
         "Parcel Processing Centre"),
        ("NSW", "Sydney Parcel Facility", "Chullora",
         "Parcel Processing Centre"),
        ("QLD", "Brisbane Parcel Facility", "Redbank",
         "Parcel Processing Centre"),
        ("SA", "Netley Parcel Facility", "Netley",
         "Parcel Processing Centre"),
        ("WA", "Perth Parcel Facility", "Welshpool",
         "Parcel Processing Centre"),
        ("TAS", "Launceston Parcel Centre", "St Leonards",
         "Parcel Processing Centre"),
        ("ACT", "North Canberra Business Hub", "North Canberra",
         "Geographic Proxy - Business Hub"),
        ("NT", "Winnellie Business Hub", "Winnellie",
         "Geographic Proxy - Business Hub")
    ]

    # --------------------------------------------------------
    # 20.2 CREATE REFERENCE TABLE
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_location_references (
                state VARCHAR PRIMARY KEY,
                reference_site_name VARCHAR NOT NULL,
                reference_suburb VARCHAR NOT NULL,
                reference_type VARCHAR NOT NULL,
                reference_source VARCHAR NOT NULL,
                model_purpose VARCHAR NOT NULL
            )
        """)

        # ----------------------------------------------------
        # 20.3 INSERT LOCATIONS WITHOUT DUPLICATES
        # ----------------------------------------------------

        for state, site_name, suburb, reference_type in reference_locations:

            # Preserve existing records on repeated runs.
            con.execute("""
                INSERT INTO mip_location_references (
                    state,
                    reference_site_name,
                    reference_suburb,
                    reference_type,
                    reference_source,
                    model_purpose
                )
                SELECT ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM mip_location_references
                    WHERE state = ?
                )
            """, [
                state,
                site_name,
                suburb,
                reference_type,
                "Australia Post public website",
                "Geographic reference for synthetic MIP",
                state
            ])

    # --------------------------------------------------------
    # 20.4 CONFIRM INITIALISATION
    # --------------------------------------------------------

    print("MIP geographic references initialised.")
    print("Reference locations: 8")
    print("Source: Public Australia Post information")
    print("Synthetic operational data remains unchanged.")

# ============================================================
# STEP 21: LINK ACTIVE FACILITIES TO GEOGRAPHIC REFERENCES
# ============================================================

def get_facility_location_references():
    """
    Match synthetic Active facilities to publicly documented
    geographic reference locations using their state.

    This is a reference mapping only.

    It does not:
        - Change facility coordinates
        - Modify the historical parcel database
        - Claim synthetic facilities are actual Australia Post sites
        - Change candidate facility status
    """

    # --------------------------------------------------------
    # 21.1 RETRIEVE FACILITIES AND LOCATION REFERENCES
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        # LEFT JOIN preserves facilities even if no reference
        # location exists for their state.
        records = con.execute("""
            SELECT
                f.facility_id,
                f.facility_name,
                f.state,
                f.status,
                f.annual_capacity,

                r.reference_site_name,
                r.reference_suburb,
                r.reference_type,
                r.reference_source

            FROM facility_registry AS f

            LEFT JOIN mip_location_references AS r
                ON f.state = r.state

            WHERE f.status = 'Active'

            ORDER BY f.state, f.facility_id
        """).fetchall()

    # --------------------------------------------------------
    # 21.2 CONVERT RESULTS INTO DICTIONARIES
    # --------------------------------------------------------

    mapped_facilities = []
    missing_references = []

    for record in records:

        (
            facility_id,
            facility_name,
            state,
            status,
            annual_capacity,
            reference_site,
            reference_suburb,
            reference_type,
            reference_source
        ) = record

        # Identify whether a geographic reference was found.
        has_reference = reference_site is not None

        facility_record = {
            "facility_id": facility_id,
            "facility_name": facility_name,
            "state": state,
            "status": status,
            "annual_capacity": annual_capacity,
            "reference_site": reference_site,
            "reference_suburb": reference_suburb,
            "reference_type": reference_type,
            "reference_source": reference_source,
            "has_reference": has_reference
        }

        mapped_facilities.append(facility_record)

        # Record missing references for later investigation.
        if not has_reference:
            missing_references.append(facility_id)

    # --------------------------------------------------------
    # 21.3 DISPLAY GEOGRAPHIC MAPPING
    # --------------------------------------------------------

    print("\nMIP FACILITY GEOGRAPHIC REFERENCES")
    print("-" * 70)

    if not mapped_facilities:
        print("No Active facilities found.")

    for facility in mapped_facilities:

        print(
            f"\nFacility: {facility['facility_id']}"
            f" | {facility['facility_name']}"
        )

        print(f"State: {facility['state']}")

        if facility["has_reference"]:

            print(
                f"Reference site: {facility['reference_site']}"
            )

            print(
                f"Reference suburb: {facility['reference_suburb']}"
            )

            print(
                f"Reference type: {facility['reference_type']}"
            )

        else:
            print("Reference location: NOT FOUND")

    # --------------------------------------------------------
    # 21.4 DISPLAY AND RETURN VALIDATION SUMMARY
    # --------------------------------------------------------

    matched_count = (
        len(mapped_facilities) - len(missing_references)
    )

    print("\nGEOGRAPHIC REFERENCE SUMMARY")
    print("-" * 70)

    print(f"Active facilities: {len(mapped_facilities)}")
    print(f"Facilities with references: {matched_count}")
    print(f"Facilities missing references: {len(missing_references)}")

    print(
        "Note: These are geographic proxies for a synthetic "
        "MIP network, not verified facility-to-site assignments."
    )

    return {
        "mapped_facilities": mapped_facilities,
        "active_facility_count": len(mapped_facilities),
        "matched_count": matched_count,
        "missing_references": missing_references
    }

# ============================================================
# STEP 20A: IMPORT EXISTING FACILITIES INTO MIP NETWORK
# ============================================================

def import_existing_facilities():
    """
    Import the eight existing synthetic facilities from
    parcel_delivery.duckdb into mip_network.duckdb.

    The original database is read-only.

    Imported facilities are registered as Active.
    Existing records are preserved.

    Capacity and fixed costs are temporary MIP assumptions,
    not actual Australia Post operating figures.
    """

    # --------------------------------------------------------
    # 20.1 LOCATE THE HISTORICAL DATABASE
    # --------------------------------------------------------

    source_db_path = (
        PROJECT_ROOT
        / "data"
        / "processed"
        / "parcel_delivery.duckdb"
    )

    # Verify the historical database exists.
    if not source_db_path.is_file():
        raise FileNotFoundError(
            f"Historical database not found: {source_db_path}"
        )

    # --------------------------------------------------------
    # 20.2 READ HISTORICAL FACILITIES
    # --------------------------------------------------------

    # Read-only mode protects the original parcel database.
    with duckdb.connect(
        str(source_db_path),
        read_only=True
    ) as source_con:

        facilities = source_con.execute("""
            SELECT
                facility_id,
                facility_name,
                state,
                facility_type
            FROM facilities
            ORDER BY facility_id
        """).fetchall()

    print(f"\nHistorical facilities found: {len(facilities)}")

    if not facilities:
        raise ValueError("No historical facilities found.")

    # --------------------------------------------------------
    # 20.3 REGISTER FACILITIES IN THE MIP DATABASE
    # --------------------------------------------------------

    # Hypothetical assumptions for initial MIP testing.
    default_annual_capacity = 300_000
    default_annual_fixed_cost_aud = 250_000

    imported_count = 0
    skipped_count = 0

    with duckdb.connect(str(DB_PATH)) as con:

        for facility_id, facility_name, state, facility_type in facilities:

            # Check whether this facility already exists.
            existing = con.execute("""
                SELECT COUNT(*)
                FROM facility_registry
                WHERE facility_id = ?
            """, [facility_id]).fetchone()[0]

            # Never overwrite existing records.
            if existing > 0:
                print(f"Skipped {facility_id}: already exists.")
                skipped_count += 1
                continue

            # Convert the historical facility category
            # into a temporary MIP classification.
            mip_facility_type = "Major Hub"

            # Insert the existing synthetic facility as Active.
            con.execute("""
                INSERT INTO facility_registry (
                    facility_id,
                    facility_name,
                    state,
                    facility_type,
                    annual_capacity,
                    annual_fixed_cost_aud,
                    latitude,
                    longitude,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                facility_id,
                facility_name,
                state,
                mip_facility_type,
                default_annual_capacity,
                default_annual_fixed_cost_aud,
                None,
                None,
                "Active"
            ])

            imported_count += 1

            print(
                f"Imported {facility_id}: "
                f"{facility_name} ({state})"
            )

    # --------------------------------------------------------
    # 20.4 DISPLAY IMPORT SUMMARY
    # --------------------------------------------------------

    print("\nHISTORICAL FACILITY IMPORT SUMMARY")
    print("-" * 60)

    print(f"Source facilities: {len(facilities)}")
    print(f"Newly imported: {imported_count}")
    print(f"Already registered: {skipped_count}")

    print("Original parcel records: unchanged")
    print("Capacity and cost values: synthetic assumptions")

    return {
        "source_facilities": len(facilities),
        "imported": imported_count,
        "skipped": skipped_count
    }

# ============================================================
# STEP 22: VALIDATE MIP NETWORK AND GEOGRAPHIC REFERENCES
# ============================================================

def validate_mip_network():
    """
    Validate the MIP network before transport modelling.

    Checks:
        1. Facility registration and operational status
        2. Facility eligibility across MIP scenarios
        3. Missing or potentially unsuitable geographic references

    This function only reads data.
    It does not update facility locations or statuses.
    """

    # --------------------------------------------------------
    # 22.1 VALIDATE REGISTERED FACILITIES
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        # Retrieve the current facility registry.
        facilities = con.execute("""
            SELECT
                facility_id,
                facility_name,
                state,
                status
            FROM facility_registry
            ORDER BY facility_id
        """).fetchall()

        # Count facilities by operational status.
        status_counts = con.execute("""
            SELECT
                status,
                COUNT(*) AS facility_count
            FROM facility_registry
            GROUP BY status
        """).fetchall()

        # Retrieve scenarios and their candidate policies.
        scenarios = con.execute("""
            SELECT
                scenario_id,
                demand_multiplier,
                allow_candidate_facilities
            FROM mip_scenarios
            ORDER BY scenario_id
        """).fetchall()

        # ----------------------------------------------------
        # 22.2 RETRIEVE GEOGRAPHIC REFERENCE MAPPINGS
        # ----------------------------------------------------

        # LEFT JOIN ensures facilities with missing
        # geographic references are still returned.
        geographic_records = con.execute("""
            SELECT
                f.facility_id,
                f.facility_name,
                f.state,
                f.status,
                r.reference_site_name,
                r.reference_suburb,
                r.reference_type
            FROM facility_registry AS f

            LEFT JOIN mip_location_references AS r
                ON f.state = r.state

            WHERE f.status = 'Active'
            ORDER BY f.facility_id
        """).fetchall()

    # --------------------------------------------------------
    # 22.3 VALIDATE SCENARIO ELIGIBILITY
    # --------------------------------------------------------

    print("\nMIP NETWORK VALIDATION")
    print("=" * 70)

    print("\nFACILITY STATUS SUMMARY")
    print("-" * 70)

    for status, count in status_counts:
        print(f"{status}: {count}")

    scenario_results = {}

    print("\nSCENARIO ELIGIBILITY")
    print("-" * 70)

    for scenario_id, multiplier, allow_candidates in scenarios:

        # Active facilities are always eligible.
        eligible_facilities = [
            facility
            for facility in facilities
            if facility[3] == "Active"
            or (
                allow_candidates
                and facility[3] == "Candidate"
            )
        ]

        scenario_results[scenario_id] = {
            "demand_multiplier": multiplier,
            "eligible_count": len(eligible_facilities),
            "eligible_facility_ids": [
                facility[0] for facility in eligible_facilities
            ]
        }

        print(
            f"{scenario_id} | "
            f"Demand multiplier: {multiplier:.2f} | "
            f"Eligible facilities: {len(eligible_facilities)}"
        )

    # --------------------------------------------------------
    # 22.4 FLAG GEOGRAPHIC REFERENCE LIMITATIONS
    # --------------------------------------------------------

    print("\nGEOGRAPHIC REFERENCE VALIDATION")
    print("-" * 70)

    geographic_warnings = []

    for record in geographic_records:

        (
            facility_id,
            facility_name,
            state,
            status,
            reference_site,
            reference_suburb,
            reference_type
        ) = record

        # Check whether a reference exists for the state.
        if reference_site is None:

            warning = (
                f"{facility_id}: No geographic reference found."
            )

            geographic_warnings.append(warning)
            print("WARNING:", warning)
            continue

        # Flag business hubs being used as geographic proxies.
        if reference_type.startswith("Geographic Proxy"):

            warning = (
                f"{facility_id}: {reference_site} is a "
                f"business-hub proxy, not a verified "
                f"parcel-processing facility assignment."
            )

            geographic_warnings.append(warning)
            print("WARNING:", warning)

        # Specific known mismatch from our synthetic network:
        # F006 represents Hobart, whereas its state-level
        # reference points to St Leonards near Launceston.
        if (
            facility_id == "F006"
            and state == "TAS"
            and reference_suburb == "St Leonards"
        ):

            warning = (
                "F006: Synthetic Hobart facility is mapped "
                "to a St Leonards/Launceston reference. "
                "A Hobart-based geographic proxy is needed."
            )

            geographic_warnings.append(warning)
            print("WARNING:", warning)

    # --------------------------------------------------------
    # 22.5 RETURN VALIDATION RESULTS
    # --------------------------------------------------------

    print("\nVALIDATION SUMMARY")
    print("-" * 70)

    print(f"Total registered facilities: {len(facilities)}")
    print(f"Geographic warnings: {len(geographic_warnings)}")

    print(
        "Geographic references remain separate "
        "from synthetic operational records."
    )

    return {
        "total_facilities": len(facilities),
        "status_counts": dict(status_counts),
        "scenario_results": scenario_results,
        "geographic_warnings": geographic_warnings
    }

# ============================================================
# STEP 23: INITIALISE MIP FACILITY COORDINATES TABLE
# ============================================================

def initialise_coordinates_table():
    """
    Create a table for geographic coordinates used by
    the synthetic MIP network optimisation model.

    Each facility can have one coordinate record.

    Coordinates can represent:
        - Public facility references
        - Approximate geographic proxies
        - Hypothetical candidate locations

    Existing coordinate records are preserved.
    """

    # --------------------------------------------------------
    # 23.1 CONNECT TO MIP DATABASE
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        # ----------------------------------------------------
        # 23.2 CREATE COORDINATES TABLE
        # ----------------------------------------------------

        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_facility_coordinates (

                facility_id VARCHAR PRIMARY KEY,

                latitude DOUBLE NOT NULL,

                longitude DOUBLE NOT NULL,

                location_name VARCHAR NOT NULL,

                location_type VARCHAR NOT NULL,

                coordinate_source VARCHAR NOT NULL,

                verification_status VARCHAR NOT NULL,

                source_url VARCHAR,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (facility_id)
                    REFERENCES facility_registry(facility_id),

                CHECK (latitude BETWEEN -90 AND 90),

                CHECK (longitude BETWEEN -180 AND 180),

                CHECK (
                    location_type IN (
                        'Public Facility Reference',
                        'Geographic Proxy',
                        'Hypothetical Candidate'
                    )
                ),

                CHECK (
                    verification_status IN (
                        'Verified',
                        'Approximate',
                        'Hypothetical'
                    )
                )
            )
        """)

        # ----------------------------------------------------
        # 23.3 VERIFY TABLE STRUCTURE
        # ----------------------------------------------------

        # Count existing geographic coordinate records.
        coordinate_count = con.execute("""
            SELECT COUNT(*)
            FROM mip_facility_coordinates
        """).fetchone()[0]

    # --------------------------------------------------------
    # 23.4 DISPLAY INITIALISATION RESULT
    # --------------------------------------------------------

    print("\nMIP coordinates table initialised successfully.")
    print(f"Registered coordinate records: {coordinate_count}")
    print("Existing facility and parcel data: unchanged")


# ============================================================
# STEP 24: REGISTER FACILITY GEOGRAPHIC COORDINATES
# ============================================================

def register_facility_coordinates(
    facility_id,
    latitude,
    longitude,
    location_name,
    location_type,
    coordinate_source,
    verification_status,
    source_url=None
):
    """
    Register one geographic coordinate record for the MIP model.

    Existing records are preserved.
    No facility statuses or historical parcels are changed.
    """

    # --------------------------------------------------------
    # 24.1 VALIDATE INPUTS
    # --------------------------------------------------------

    valid_types = {
        "Public Facility Reference",
        "Geographic Proxy",
        "Hypothetical Candidate"
    }

    valid_statuses = {
        "Verified",
        "Approximate",
        "Hypothetical"
    }

    if not isinstance(facility_id, str) or not facility_id.strip():
        raise ValueError("Facility ID must be non-empty text.")

    if not isinstance(location_name, str) or not location_name.strip():
        raise ValueError("Location name must be non-empty text.")

    if not isinstance(coordinate_source, str) or not coordinate_source.strip():
        raise ValueError("Coordinate source must be non-empty text.")

    if (
        not isinstance(latitude, (int, float))
        or isinstance(latitude, bool)
        or not -90 <= latitude <= 90
    ):
        raise ValueError("Invalid latitude.")

    if (
        not isinstance(longitude, (int, float))
        or isinstance(longitude, bool)
        or not -180 <= longitude <= 180
    ):
        raise ValueError("Invalid longitude.")

    if location_type not in valid_types:
        raise ValueError("Invalid location type.")

    if verification_status not in valid_statuses:
        raise ValueError("Invalid verification status.")

    # --------------------------------------------------------
    # 24.2 VERIFY FACILITY AND CHECK DUPLICATES
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:

        facility = con.execute("""
            SELECT facility_id
            FROM facility_registry
            WHERE facility_id = ?
        """, [facility_id]).fetchone()

        if facility is None:
            raise ValueError(
                f"Facility {facility_id} is not registered."
            )

        existing = con.execute("""
            SELECT facility_id
            FROM mip_facility_coordinates
            WHERE facility_id = ?
        """, [facility_id]).fetchone()

        if existing is not None:
            print(f"Coordinates for {facility_id} already exist.")
            return

        # ----------------------------------------------------
        # 24.3 INSERT COORDINATE RECORD
        # ----------------------------------------------------

        con.execute("""
            INSERT INTO mip_facility_coordinates (
                facility_id,
                latitude,
                longitude,
                location_name,
                location_type,
                coordinate_source,
                verification_status,
                source_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            facility_id,
            latitude,
            longitude,
            location_name,
            location_type,
            coordinate_source,
            verification_status,
            source_url
        ])

    # --------------------------------------------------------
    # 24.4 DISPLAY RESULT
    # --------------------------------------------------------

    print(
        f"Coordinates registered: {facility_id} "
        f"| {location_name} "
        f"| ({latitude}, {longitude})"
    )


# ============================================================
# STEP 25: VALIDATE MIP FACILITY COORDINATE COVERAGE
# ============================================================

def validate_facility_coordinates():
    """
    Validate facility and coordinate records before
    constructing the MIP transportation distance matrix.

    Checks:
        1. Total Active and Candidate facilities
        2. Missing geographic coordinate records
        3. Invalid latitude and longitude values
        4. Consistency of candidate location classifications

    This function does not modify the database.
    """

    # --------------------------------------------------------
    # 25.1 RETRIEVE FACILITIES AND COORDINATES
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        records = con.execute("""
            SELECT
                f.facility_id,
                f.facility_name,
                f.state,
                f.status,
                f.annual_capacity,

                c.latitude,
                c.longitude,
                c.location_name,
                c.location_type,
                c.verification_status

            FROM facility_registry AS f

            LEFT JOIN mip_facility_coordinates AS c
                ON f.facility_id = c.facility_id

            WHERE f.status IN ('Active', 'Candidate')
            ORDER BY f.status, f.state, f.facility_id
        """).fetchall()

    # --------------------------------------------------------
    # 25.2 INITIALISE VALIDATION COUNTERS
    # --------------------------------------------------------

    active_count = 0
    candidate_count = 0
    valid_count = 0

    missing_coordinates = []
    invalid_coordinates = []
    classification_warnings = []

    print("\nMIP FACILITY COORDINATE VALIDATION")
    print("=" * 70)

    # --------------------------------------------------------
    # 25.3 VALIDATE EACH FACILITY
    # --------------------------------------------------------

    for record in records:

        (
            facility_id,
            facility_name,
            state,
            status,
            annual_capacity,
            latitude,
            longitude,
            location_name,
            location_type,
            verification_status
        ) = record

        if status == "Active":
            active_count += 1

        if status == "Candidate":
            candidate_count += 1

        # Check whether a coordinate record exists.
        if latitude is None or longitude is None:

            missing_coordinates.append(facility_id)

            print(
                f"MISSING | {facility_id} | "
                f"{facility_name}"
            )

            continue

        # Validate geographic coordinate ranges.
        if not (-90 <= latitude <= 90):
            invalid_coordinates.append(facility_id)
            continue

        if not (-180 <= longitude <= 180):
            invalid_coordinates.append(facility_id)
            continue

        # Check that proposed candidate facilities are
        # clearly identified as hypothetical.
        if status == "Candidate":

            if (
                location_type != "Hypothetical Candidate"
                or verification_status != "Hypothetical"
            ):

                classification_warnings.append(facility_id)

        valid_count += 1

        print(
            f"VALID | {facility_id} | "
            f"{status} | "
            f"({latitude:.4f}, {longitude:.4f})"
        )

    # --------------------------------------------------------
    # 25.4 CALCULATE OVERALL COVERAGE
    # --------------------------------------------------------

    total_facilities = len(records)

    coverage_pct = (
        valid_count / total_facilities * 100
        if total_facilities > 0
        else 0
    )

    # Require complete records before distance modelling.
    ready_for_distance_modelling = (
        total_facilities > 0
        and len(missing_coordinates) == 0
        and len(invalid_coordinates) == 0
        and len(classification_warnings) == 0
    )

    # --------------------------------------------------------
    # 25.5 DISPLAY VALIDATION SUMMARY
    # --------------------------------------------------------

    print("\nCOORDINATE VALIDATION SUMMARY")
    print("-" * 70)

    print(f"Active facilities: {active_count}")
    print(f"Candidate facilities: {candidate_count}")
    print(f"Total facilities: {total_facilities}")
    print(f"Valid coordinate records: {valid_count}")

    print(f"Missing coordinates: {len(missing_coordinates)}")
    print(f"Invalid coordinates: {len(invalid_coordinates)}")

    print(
        f"Candidate classification warnings: "
        f"{len(classification_warnings)}"
    )

    print(f"Coordinate coverage: {coverage_pct:.2f}%")

    print(
        f"Ready for distance modelling: "
        f"{ready_for_distance_modelling}"
    )

    # --------------------------------------------------------
    # 25.6 RETURN STRUCTURED VALIDATION RESULTS
    # --------------------------------------------------------

    return {
        "active_count": active_count,
        "candidate_count": candidate_count,
        "total_facilities": total_facilities,
        "valid_coordinate_count": valid_count,
        "missing_coordinates": missing_coordinates,
        "invalid_coordinates": invalid_coordinates,
        "classification_warnings": classification_warnings,
        "coverage_pct": round(coverage_pct, 2),
        "ready_for_distance_modelling": ready_for_distance_modelling
    }

# ============================================================
# STEP 5: REGISTER AND VERIFY A NEW FACILITY
# ============================================================

if __name__ == "__main__":

    # 1. Create the database/table if necessary.
    initialise_database()
    # Step 7: Initialise facility leases.
    initialise_lease_table()
    # Step 10: Initialise staff table
    initialise_staffing_table()
    # STEP 13: Initialise maintenance costs.
    initialise_maintenance_table()
    # STEP 20: Register geographic references for the MIP model.
    initialise_location_references()

    # 2. Register a new candidate facility.
    create_facility(
        facility_id="VIC_H09",
        facility_name="Melbourne West Hub",
        state="VIC",
        facility_type="Local Facility",
        annual_capacity=35_000,
        annual_fixed_cost_aud=55_000,
        latitude=-37.82,
        longitude=144.70
    )

    # 3. Verify the record was saved in DuckDB.
    with duckdb.connect(str(DB_PATH)) as con:

        facility = con.execute("""
            SELECT
                facility_id,
                facility_name,
                state,
                status,
                annual_capacity
            FROM facility_registry
            WHERE facility_id = ?
        """, ["VIC_H09"]).fetchone()

        total_facilities = con.execute("""
            SELECT COUNT(*)
            FROM facility_registry
        """).fetchone()[0]

    print("\nRegistered facility:", facility)
    print("Total facilities:", total_facilities)


    # ========================================================
    # STEP 7: VERIFY THE FACILITY LEASES TABLE
    # ========================================================

    with duckdb.connect(str(DB_PATH)) as con:

        tables = con.execute("""
            SHOW TABLES
        """).fetchall()

        lease_count = con.execute("""
            SELECT COUNT(*)
            FROM facility_leases
        """).fetchone()[0]

    print("\nDATABASE TABLES")
    for table in tables:
        print(table[0])

    print(f"\nNumber of registered leases: {lease_count}")

    # ========================================================
    # STEP 8: REGISTER FIRST LEASE
    # ========================================================

    create_lease(
        lease_id="LEASE_VIC_H09_001",
        facility_id="VIC_H09",
        lease_start_date="2027-01-01",
        lease_term_months=36,
        rent_amount_aud=36_000,
        payment_frequency="Quarterly",
        setup_cost_aud=80_000,
        annual_rent_increase_pct=3.0,
        renewal_option=True,
        renewal_term_months=24
    )
    # ========================================================
    # STEP 9: CALCULATE LEASE COSTS
    # ========================================================

    lease_forecast = calculate_lease_costs(
        lease_id="LEASE_VIC_H09_001"
    )

    # ========================================================
    # STEP 11: REGISTER STAFFING FOR VIC_H09
    # ========================================================

    create_staffing_requirement(
        staffing_id="STAFF_VIC_H09_001",
        facility_id="VIC_H09",
        role_name="Operations Supervisor",
        employment_type="Full-time",
        minimum_headcount=1,
        annual_salary_per_employee_aud=85_000,
        employer_oncost_pct=20,
        shift_name="Day",
        operating_days_per_week=5
    )

    create_staffing_requirement(
        staffing_id="STAFF_VIC_H09_002",
        facility_id="VIC_H09",
        role_name="Parcel Processing Operator",
        employment_type="Full-time",
        minimum_headcount=4,
        annual_salary_per_employee_aud=60_000,
        employer_oncost_pct=20,
        shift_name="Day",
        operating_days_per_week=5
    )

    create_staffing_requirement(
        staffing_id="STAFF_VIC_H09_003",
        facility_id="VIC_H09",
        role_name="Maintenance Technician",
        employment_type="Full-time",
        minimum_headcount=1,
        annual_salary_per_employee_aud=75_000,
        employer_oncost_pct=20,
        shift_name="Flexible",
        operating_days_per_week=5
    )

    # ========================================================
    # STEP 12: CALCULATE FACILITY STAFFING COSTS
    # ========================================================

    staffing_summary = calculate_staffing_costs(
        facility_id="VIC_H09"
    )

    # ========================================================
    # STEP 14: REGISTER MAINTENANCE EXPENSES
    # ========================================================

    # Quarterly conveyor and sorting equipment servicing.
    create_maintenance_expense(
        maintenance_id="MAINT_VIC_H09_001",
        facility_id="VIC_H09",
        maintenance_category="Preventive",
        expense_description="Conveyor and sorting equipment servicing",
        cost_amount_aud=2_500,
        payment_frequency="Quarterly",
        cost_type="Fixed",
        annual_cost_increase_pct=3.0
    )

    # Annual electrical and safety inspections.
    create_maintenance_expense(
        maintenance_id="MAINT_VIC_H09_002",
        facility_id="VIC_H09",
        maintenance_category="Inspection",
        expense_description="Electrical and safety inspections",
        cost_amount_aud=3_000,
        payment_frequency="Annually",
        cost_type="Fixed",
        annual_cost_increase_pct=3.0
    )

    # One-time initial replacement parts inventory.
    create_maintenance_expense(
        maintenance_id="MAINT_VIC_H09_003",
        facility_id="VIC_H09",
        maintenance_category="Spare Parts",
        expense_description="Initial replacement parts inventory",
        cost_amount_aud=5_000,
        payment_frequency="One-time",
        cost_type="Fixed",
        annual_cost_increase_pct=0
    )

    # ========================================================
    # STEP 15: CALCULATE FACILITY MAINTENANCE COSTS
    # ========================================================

    maintenance_summary = calculate_maintenance_costs(
        facility_id="VIC_H09"
    )

    # ========================================================
    # STEP 16: CALCULATE TOTAL FACILITY COSTS
    # ========================================================

    facility_cost_summary = calculate_facility_costs(
        facility_id="VIC_H09"
    )

    # ========================================================
    # STEP 16B: KEEP ONE FIXED-COST FIGURE PER FACILITY
    # ========================================================

    fixed_cost_check = sync_facility_fixed_costs()

    # ========================================================
    # STEP 20A: IMPORT HISTORICAL FACILITIES INTO MIP
    # ========================================================

    # This runs before Step 19 so that the baseline scenario
    # can see the eight Active facilities on a first run.

    # Read the eight existing synthetic processing facilities
    # from parcel_delivery.duckdb.

    # Register them as Active in mip_network.duckdb
    # without modifying historical parcel records.

    import_summary = import_existing_facilities()

    # ========================================================
    # STEP 17: INITIALISE MIP SCENARIOS
    # ========================================================

    initialise_scenarios_table()

    # ========================================================
    # STEP 18: REGISTER MIP OPTIMISATION SCENARIOS
    # ========================================================

    # Scenario 1: Baseline network.
    # Only existing Active facilities are eligible.
    create_scenario(
        scenario_id="BASE_2027",
        scenario_name="2027 Baseline Network",
        description="Evaluate the existing network without opening candidate facilities.",
        planning_year=2027,
        demand_multiplier=1.0,
        allow_candidate_facilities=False,
        optimisation_objective="Minimise Total Cost",
        scenario_status="Draft"
    )

    # Scenario 2: Network expansion.
    # Candidate facilities can be considered by the MIP.
    create_scenario(
        scenario_id="EXPANSION_2027",
        scenario_name="2027 Network Expansion",
        description="Evaluate opening candidate facilities to minimise total network costs.",
        planning_year=2027,
        demand_multiplier=1.0,
        allow_candidate_facilities=True,
        optimisation_objective="Minimise Total Cost",
        scenario_status="Draft"
    )

    # Scenario 3: Increased parcel demand.
    # Assume 25% higher demand than the baseline.
    create_scenario(
        scenario_id="PEAK_2027",
        scenario_name="2027 Peak Demand",
        description="Evaluate network capacity under a 25 percent demand increase.",
        planning_year=2027,
        demand_multiplier=1.25,
        allow_candidate_facilities=True,
        optimisation_objective="Minimise Total Cost",
        scenario_status="Draft"
    )

    # ========================================================
    # STEP 19: CHECK FACILITY ELIGIBILITY BY SCENARIO
    # ========================================================

    # Baseline: Only Active facilities.
    baseline_facilities = get_scenario_facilities(
        scenario_id="BASE_2027"
    )

    # Expansion: Active and Candidate facilities.
    expansion_facilities = get_scenario_facilities(
        scenario_id="EXPANSION_2027"
    )

    # Peak: Active and Candidate facilities.
    peak_facilities = get_scenario_facilities(
        scenario_id="PEAK_2027"
    )


    # ========================================================
    # STEP 21: VERIFY GEOGRAPHIC REFERENCES
    # ========================================================

    # Match imported Active facilities to public
    # geographic reference locations using their states.

    location_summary = get_facility_location_references()


    # ========================================================
    # STEP 22: VALIDATE MIP NETWORK
    # ========================================================

    network_validation = validate_mip_network()


    # ========================================================
    # STEP 23: INITIALISE MIP GEOGRAPHIC COORDINATES
    # ========================================================

    # Create the table that will store location coordinates
    # and their associated source and verification metadata.

    initialise_coordinates_table()

    # ========================================================
    # STEP 24.7: DEFINE HYPOTHETICAL CANDIDATE FACILITIES
    # ========================================================

    # Facility ID, name, state, type, annual capacity,
    # provisional fixed cost, latitude and longitude.
    #
    # Costs and coordinates are synthetic modelling inputs.
    # Fixed cost = 0 means not yet estimated, NOT free to operate.

    new_candidates = [
        ("VIC_H10", "Dandenong South Hub", "VIC",
         "Regional Hub", 80_000, 0, -38.030, 145.210),

        ("VIC_H11", "Truganina Hub", "VIC",
         "Major Hub", 120_000, 0, -37.820, 144.750),

        ("NSW_H09", "Eastern Creek Hub", "NSW",
         "Major Hub", 120_000, 0, -33.805, 150.870),

        ("NSW_H10", "Newcastle Hub", "NSW",
         "Regional Hub", 60_000, 0, -32.930, 151.780),

        ("QLD_H09", "Ipswich Hub", "QLD",
         "Regional Hub", 80_000, 0, -27.615, 152.760),

        ("SA_H09", "Edinburgh Hub", "SA",
         "Regional Hub", 60_000, 0, -34.710, 138.620),

        ("WA_H09", "Kewdale Hub", "WA",
         "Regional Hub", 80_000, 0, -31.980, 115.970),

        ("TAS_H09", "Launceston Hub", "TAS",
         "Local Facility", 35_000, 0, -41.440, 147.140)
    ]

    # ========================================================
    # STEP 24.8: REGISTER ALL NEW CANDIDATE FACILITIES
    # ========================================================

    for (
        facility_id,
        facility_name,
        state,
        facility_type,
        annual_capacity,
        annual_fixed_cost_aud,
        latitude,
        longitude
    ) in new_candidates:

        # 1. Register the candidate in facility_registry.
        create_facility(
            facility_id=facility_id,
            facility_name=facility_name,
            state=state,
            facility_type=facility_type,
            annual_capacity=annual_capacity,
            annual_fixed_cost_aud=annual_fixed_cost_aud,
            latitude=latitude,
            longitude=longitude
        )

        # 2. Register its hypothetical geographic coordinates.
        register_facility_coordinates(
            facility_id=facility_id,
            latitude=latitude,
            longitude=longitude,
            location_name=facility_name,
            location_type="Hypothetical Candidate",
            coordinate_source="Synthetic MIP candidate assumption",
            verification_status="Hypothetical"
        )

    # 3. Verify the number of facilities by status.
    with duckdb.connect(str(DB_PATH), read_only=True) as con:

        facility_counts = con.execute("""
            SELECT
                status,
                COUNT(*) AS facility_count
            FROM facility_registry
            GROUP BY status
            ORDER BY status
        """).fetchall()

    print("\nUPDATED MIP FACILITY REGISTRY")
    print("-" * 60)

    for status, count in facility_counts:
        print(f"{status}: {count}")


    # ========================================================
    # STEP 25: COMPLETE MISSING FACILITY COORDINATES
    # ========================================================

    # Approximate city-centre geographic proxies.
    # These are not actual Australia Post facility locations.

    existing_locations = [
        ("F001", -37.8136, 144.9631, "Melbourne"),
        ("F002", -33.8688, 151.2093, "Sydney"),
        ("F003", -27.4698, 153.0251, "Brisbane"),
        ("F004", -34.9285, 138.6007, "Adelaide"),
        ("F005", -31.9523, 115.8613, "Perth"),
        ("F006", -42.8821, 147.3272, "Hobart"),
        ("F007", -35.2809, 149.1300, "Canberra"),
        ("F008", -12.4634, 130.8456, "Darwin")
    ]

    # --------------------------------------------------------
    # 25.1 REGISTER EXISTING ACTIVE FACILITIES
    # --------------------------------------------------------

    for facility_id, latitude, longitude, city in existing_locations:

        register_facility_coordinates(
            facility_id=facility_id,
            latitude=latitude,
            longitude=longitude,
            location_name=f"{city} City Centre",
            location_type="Geographic Proxy",
            coordinate_source="Approximate city-centre modelling coordinates",
            verification_status="Approximate"
        )

    # --------------------------------------------------------
    # 25.2 REGISTER ORIGINAL MELBOURNE WEST CANDIDATE
    # --------------------------------------------------------

    register_facility_coordinates(
        facility_id="VIC_H09",
        latitude=-37.82,
        longitude=144.70,
        location_name="Proposed Melbourne West Hub",
        location_type="Hypothetical Candidate",
        coordinate_source="Synthetic MIP candidate assumption",
        verification_status="Hypothetical"
    )

    # --------------------------------------------------------
    # 25.3 VALIDATE COMPLETE COORDINATE COVERAGE
    # --------------------------------------------------------

    coordinate_validation = validate_facility_coordinates()

    # ========================================================
    # NEXT: STEP 26 (DISTANCE MATRIX)
    # ========================================================

    # The distance matrix is calculated in one place only:
    #     python src/mip_transport_costs.py
    print("\nNext step: python src/mip_transport_costs.py")