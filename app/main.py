"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
import re

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import (
    MongoClient,
    ASCENDING,
    DESCENDING,
    ReturnDocument,
)
from pymongo.errors import DuplicateKeyError


load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=5000,
)

db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)


@app.on_event("startup")
def create_indexes():
    # Each employee code must be unique.
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="employee_code_unique",
    )

    # Only one attendance record per employee per date.
    db.attendance_logs.create_index(
        [
            ("emp_code", ASCENDING),
            ("date", DESCENDING),
        ],
        unique=True,
        name="attendance_employee_date_unique",
    )

    # Support date filters and descending date sorting.
    db.attendance_logs.create_index(
        [
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ],
        name="attendance_date_employee",
    )

    # Support department headcount by joining date.
    db.employees.create_index(
        [
            ("department", ASCENDING),
            ("joined_on", ASCENDING),
        ],
        name="employee_department_joined_on",
    )

    # Support department-based employee queries.
    db.employees.create_index(
        [
            ("department", ASCENDING),
            ("emp_code", ASCENDING),
        ],
        name="employee_department_code",
    )


# --------------------------------------------------------------------------- #
# -------------------------Helpers--------------------------------------------#
def epoch_ms_to_datetime(value: int) -> datetime:
    """Convert epoch milliseconds to a whole-second UTC datetime."""
    return datetime.fromtimestamp(
        value / 1000,
        tz=UTC,
    ).replace(microsecond=0)

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    """Calculate late minutes using IST and a strict 10-minute grace period."""
    punch_in_ist = punch_in.astimezone(IST)

    hour, minute = map(int, shift_start.split(":"))

    shift_start_time = punch_in_ist.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    seconds_late = (
        punch_in_ist - shift_start_time
    ).total_seconds()

    # Grace period is exactly 10 minutes.
    if seconds_late <= 600:
        return 0

    return int(seconds_late // 60)


def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    """Calculate work hours rounded to 2 decimals, half-up."""
    seconds_worked = (punch_out - punch_in).total_seconds()
    hours_worked = Decimal(str(seconds_worked)) / Decimal("3600")

    return float(
        hours_worked.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
    overnight: bool = False,
) -> int:
    """Calculate overtime only when it reaches 30 full minutes."""
    hour, minute = map(int, shift_end.split(":"))

    shift_end_time = datetime.strptime(
        date_str,
        "%Y-%m-%d",
    ).replace(
        hour=hour,
        minute=minute,
        tzinfo=IST,
    )

    if overnight:
        shift_end_time += timedelta(days=1)

    punch_out_ist = punch_out.astimezone(IST)

    extra_minutes = int(
        (punch_out_ist - shift_end_time).total_seconds() // 60
    )

    if extra_minutes >= 30:
        return extra_minutes

    return 0
# --------------------------------------------------------------------------- #

#-------------------------------Models----------------------------------------#
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    department: str = Field(min_length=1, max_length=100)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.strip()

        if value.count("@") != 1:
            raise ValueError("email must be a valid email address")

        local, domain = value.split("@")

        if (
            not local
            or not domain
            or "." not in domain
            or any(char.isspace() for char in value)
            or "[" in value
            or "]" in value
            or "(" in value
            or ")" in value
        ):
            raise ValueError("email must be a valid email address")

        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("shift time must use HH:MM format")

        if parsed.strftime("%H:%M") != value:
            raise ValueError("shift time must use HH:MM format")

        return value

# --------------------------------------------------------------------------- #

class PunchInIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    status: str
    punched_at: Optional[int] = None

class PunchOutIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    punched_at: Optional[int] = None

class RegularizeRequest(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    by: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)

#--------------------------------------Endpoints provided---------------------#
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    q = {}

    if department:
        q["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    # Shift start and end cannot be the same.
    if body.shift_start == body.shift_end:
        raise HTTPException(
            status_code=422,
            detail="shift_start and shift_end must be different",
        )

    # Check whether joined_on is a real calendar date.
    try:
        datetime.strptime(body.joined_on, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="joined_on must be a valid YYYY-MM-DD date",
        )

    # Prepare the employee document.
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC)

    # Insert the employee. The unique index prevents duplicates.
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    # Never return MongoDB's internal _id.
    doc.pop("_id", None)

    # Return created_at as epoch milliseconds.
    doc["created_at"] = int(
        doc["created_at"].timestamp() * 1000
    )

    return doc

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    allowed_statuses = {"PRESENT", "WFH", "ON_DUTY"}
    if body.status not in allowed_statuses:
        raise HTTPException(
        status_code=422,
        detail="status must be PRESENT, WFH, or ON_DUTY",
    )

    # Check whether the employee exists.
    employee = db.employees.find_one(
        {"emp_code": body.emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Use the supplied timestamp or the current time.
    if body.punched_at is None:
        punch_time = datetime.now(UTC).replace(microsecond=0)
    else:
        punch_time = epoch_ms_to_datetime(body.punched_at)

    # Attendance date is based on the punch-in time in IST.
    punch_time_ist = punch_time.astimezone(IST)
    shift_start = employee.get("shift_start", "09:30")
    shift_end = employee.get("shift_end", "18:30")

    # For an overnight shift, early-morning punch-ins belong
    # to the previous attendance date.
    attendance_date = punch_time_ist.date()

    if shift_end <= shift_start and punch_time_ist.strftime("%H:%M") < shift_end:
        attendance_date = attendance_date - timedelta(days=1)

    date_str = attendance_date.isoformat()

    # Store UTC datetimes in MongoDB.
    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": punch_time,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            punch_time_ist,
            shift_start,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    # The unique index prevents duplicate attendance records,
    # including when two requests arrive concurrently.
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this employee and date",
        )

    # Return API timestamps as epoch milliseconds.
    result = doc.copy()
    result.pop("_id", None)
    result["punch_in"] = int(punch_time.timestamp() * 1000)
    return result

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    # Validate pagination.
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    # Validate date format.
    for date_value in (date_from, date_to):
        if date_value is not None:
            try:
                parsed_date = datetime.strptime(
                    date_value, "%Y-%m-%d"
                )

                if parsed_date.strftime("%Y-%m-%d") != date_value:
                    raise ValueError

            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Dates must use YYYY-MM-DD format",
                )

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from cannot be after date_to",
        )

    # Build the MongoDB query.
    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from or date_to:
        query["date"] = {}

        if date_from:
            query["date"]["$gte"] = date_from

        if date_to:
            query["date"]["$lte"] = date_to

    if status:
        query["status"] = status

    # Count matching records.
    total = db.attendance_logs.count_documents(query)

    # Fetch only the requested page.
    skip = (page - 1) * page_size

    items = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ])
        .skip(skip)
        .limit(page_size)
    )
    # Convert timestamps to epoch milliseconds
    def to_epoch_ms(value):
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return int(value)

        if isinstance(value, str):
            value = datetime.fromisoformat(value)

        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)

        return int(value.timestamp() * 1000)

    # Convert timestamps in every attendance record
    for item in items:
        item["punch_in"] = to_epoch_ms(item.get("punch_in"))
        item["punch_out"] = to_epoch_ms(item.get("punch_out"))

        for entry in item.get("history", []):
            entry["at"] = to_epoch_ms(entry.get("at"))

        if "half_day" not in item:
            item["half_day"] = False

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-out")
def punch_out(data: PunchOutIn):
    # Check whether the employee exists.
    employee = db.employees.find_one(
        {"emp_code": data.emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Use the supplied time or the current UTC time.
    punch_time = (
        epoch_ms_to_datetime(data.punched_at)
        if data.punched_at is not None
        else datetime.now(UTC).replace(microsecond=0)
    )

    # Find the most recent open attendance record.
    attendance = db.attendance_logs.find_one(
        {
            "emp_code": data.emp_code,
            "punch_out": None,
            "punch_in": {"$lte": punch_time},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if attendance is None:
        raise HTTPException(
            status_code=404,
            detail="No open punch-in record found",
        )

    punch_in_time = attendance["punch_in"]

    # MongoDB may return a datetime without timezone information.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=UTC)
    else:
        punch_in_time = punch_in_time.astimezone(UTC)

    # Do not allow punch-out after more than 24 hours.
    duration_seconds = (
        punch_time - punch_in_time
    ).total_seconds()

    if duration_seconds <= 0:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if duration_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    # Calculate working hours.
    work_hours = compute_work_hours(
        punch_in_time,
        punch_time,
    )

    # Calculate half-day using rounded working hours.
    half_day = work_hours < 4.50

    # Check whether the employee has an overnight shift.
    shift_start = employee.get("shift_start", "09:30")
    shift_end = employee.get("shift_end", "18:30")
    overnight = shift_end <= shift_start

    # Calculate overtime using the employee's shift.
    overtime_minutes = compute_overtime(
        punch_time,
        shift_end,
        attendance["date"],
        overnight,
    )

    # Update only if the record is still open.
    updated_attendance = db.attendance_logs.find_one_and_update(
        {
            "_id": attendance["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_time,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated_attendance is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance has already been punched out",
        )

    # Prepare a JSON-safe response without MongoDB's _id.
    updated_attendance.pop("_id", None)

    if updated_attendance.get("punch_in") is not None:
        updated_attendance["punch_in"] = int(
            updated_attendance["punch_in"].timestamp() * 1000
        )

    if updated_attendance.get("punch_out") is not None:
        updated_attendance["punch_out"] = int(
            updated_attendance["punch_out"].timestamp() * 1000
        )

    return updated_attendance

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    # Validate employee code.
    if not re.fullmatch(r"EMP\d{4,6}", emp_code):
        raise HTTPException(
            status_code=422,
            detail="Invalid employee code",
        )

    # Validate attendance date.
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")

        if parsed_date.strftime("%Y-%m-%d") != date:
            raise ValueError

    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Date must use YYYY-MM-DD format",
        )

    # Check that the employee exists.
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Find the attendance record using its natural key.
    attendance = db.attendance_logs.find_one(
        {
            "emp_code": emp_code,
            "date": date,
        }
    )

    if attendance is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    # Get only fields supplied by the client.
    supplied = body.model_dump(exclude_unset=True)

    # Read the current values.
    old_status = attendance["status"]
    old_punch_in = attendance.get("punch_in")
    old_punch_out = attendance.get("punch_out")

    # Convert MongoDB datetimes to timezone-aware UTC datetimes.
    def as_utc(value):
        if value is None:
            return None

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value.astimezone(UTC)

    old_punch_in = as_utc(old_punch_in)
    old_punch_out = as_utc(old_punch_out)

    new_status = supplied.get("status", old_status)
    new_punch_in = old_punch_in
    new_punch_out = old_punch_out

    allowed_statuses = {
        "PRESENT",
        "WFH",
        "ON_DUTY",
        "ABSENT",
        "LEAVE",
    }

    # Validate status.
    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    # ABSENT and LEAVE cannot be supplied with punch times.
    if new_status in {"ABSENT", "LEAVE"}:
        if "punch_in" in supplied or "punch_out" in supplied:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch times",
            )

        new_punch_in = None
        new_punch_out = None

    else:
        # Apply supplied punch-in timestamp.
        if "punch_in" in supplied:
            if supplied["punch_in"] is None:
                raise HTTPException(
                    status_code=422,
                    detail="punch_in cannot be null for a presence status",
                )

            new_punch_in = epoch_ms_to_datetime(
                supplied["punch_in"]
            )

        # Apply supplied punch-out timestamp.
        if "punch_out" in supplied:
            if supplied["punch_out"] is None:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out cannot be null for a presence status",
                )

            new_punch_out = epoch_ms_to_datetime(
                supplied["punch_out"]
            )

        # Presence statuses require a punch-in.
        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in",
            )

        # Punch-in must belong to this attendance date.
        punch_in_ist = new_punch_in.astimezone(IST)
        shift_start = employee.get("shift_start", "09:30")
        shift_end = employee.get("shift_end", "18:30")

        expected_date = punch_in_ist.date()

        if (
            shift_end <= shift_start
            and punch_in_ist.strftime("%H:%M") < shift_end
        ):
            expected_date -= timedelta(days=1)

        if expected_date.isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

        # Validate punch-out relative to punch-in.
        if new_punch_out is not None:
            duration = (
                new_punch_out - new_punch_in
            ).total_seconds()

            if duration <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )

            if duration > 24 * 60 * 60:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out cannot exceed 24 hours after punch_in",
                )

    # Build a record of fields that actually changed.
    changes = {}

    if old_status != new_status:
        changes["status"] = {
            "from": old_status,
            "to": new_status,
        }

    if old_punch_in != new_punch_in:
        changes["punch_in"] = {
            "from": (
                int(old_punch_in.timestamp() * 1000)
                if old_punch_in is not None
                else None
            ),
            "to": (
                int(new_punch_in.timestamp() * 1000)
                if new_punch_in is not None
                else None
            ),
        }

    if old_punch_out != new_punch_out:
        changes["punch_out"] = {
            "from": (
                int(old_punch_out.timestamp() * 1000)
                if old_punch_out is not None
                else None
            ),
            "to": (
                int(new_punch_out.timestamp() * 1000)
                if new_punch_out is not None
                else None
            ),
        }

    # A request must make at least one actual change.
    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the attendance record",
        )

    # Recalculate derived fields from the final values.
    if new_punch_in is None:
        late_minutes = 0
        work_hours = None
        overtime_minutes = 0
        half_day = False

    else:
        late_minutes = compute_late_minutes(
            new_punch_in,
            employee.get("shift_start", "09:30"),
        )

        if new_punch_out is None:
            work_hours = None
            overtime_minutes = 0
            half_day = False

        else:
            work_hours = compute_work_hours(
                new_punch_in,
                new_punch_out,
            )

            half_day = work_hours < 4.50

            shift_end = employee.get("shift_end", "18:30")
            overnight = shift_end <= employee.get(
                "shift_start",
                "09:30",
            )

            overtime_minutes = compute_overtime(
                new_punch_out,
                shift_end,
                date,
                overnight,
            )

    # Prepare the update.
    update_values = {
        "status": new_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    # Append a new history entry; never overwrite history.
    history_entry = {
        "at": datetime.now(UTC),
        "by": body.by,
        "reason": body.reason,
        "changes": changes,
    }

    # Only update if history is still unchanged.
    # This prevents concurrent edits from silently overwriting history.
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": attendance["_id"],
            "history": attendance.get("history", []),
        },
        {
            "$set": update_values,
            "$push": {
                "history": history_entry,
            },
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance was changed by another request; retry",
        )

    # Remove MongoDB's internal ID.
    updated.pop("_id", None)

    # Convert timestamps to epoch milliseconds for the API.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)

        if value is not None:
            value = as_utc(value)
            updated[field] = int(value.timestamp() * 1000)

    for entry in updated.get("history", []):
        entry["at"] = int(
            as_utc(entry["at"]).timestamp() * 1000
        )

    return updated

# --------------------------------------------------------------------------- #
# Monthly employee analytics
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    # Validate employee code.
    if not re.fullmatch(r"EMP\d{4,6}", emp_code):
        raise HTTPException(
            status_code=422,
            detail="Invalid employee code",
        )

    # Validate month.
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Month must use YYYY-MM format",
        )

    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month = month_start.replace(
            month=month_start.month + 1,
        )

    month_end = next_month - timedelta(days=1)

    # Find employee.
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Calculate working days from joining date.
    joined_on = datetime.strptime(
        employee["joined_on"],
        "%Y-%m-%d",
    ).date()

    first_date = max(month_start, joined_on)
    working_days = 0
    current_date = first_date

    while current_date <= month_end:
        if current_date.weekday() < 5:
            working_days += 1

        current_date += timedelta(days=1)

    # Fetch this employee's attendance for the requested month.
    records = list(
        db.attendance_logs.find(
            {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                },
            },
            {
                "_id": 0,
                "date": 1,
                "status": 1,
                "half_day": 1,
                "late_minutes": 1,
                "overtime_minutes": 1,
            },
        )
    )

    present_days = Decimal("0")
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for record in records:
        status = record.get("status")
        record_date = datetime.strptime(
            record["date"],
            "%Y-%m-%d",
        ).date()

        is_weekday = record_date.weekday() < 5

        # Present-day calculations exclude weekends.
        if status in {"PRESENT", "WFH", "ON_DUTY"} and is_weekday:
            if record.get("half_day", False):
                present_days += Decimal("0.5")
            else:
                present_days += Decimal("1")

        # Count leave records separately.
        if status == "LEAVE":
            leave_days += 1

        late_minutes = record.get("late_minutes") or 0
        overtime_minutes = record.get("overtime_minutes") or 0

        if late_minutes > 0:
            late_count += 1

        # Weekend records are still included in these totals.
        total_late_minutes += late_minutes
        total_overtime_minutes += overtime_minutes

    # Calculate attendance percentage.
    if working_days > 0:
        attendance_pct = (
            present_days * Decimal("100") / Decimal(working_days)
        ).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )

        attendance_pct = float(attendance_pct)
    else:
        attendance_pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": float(present_days),
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }
# --------------------------------------------------------------------------- #
# Department summary analytics

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    # Validate the month.
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Month must use YYYY-MM format",
        )

    # Calculate the next month's starting date.
    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month = month_start.replace(
            month=month_start.month + 1,
        )

    month_end = next_month - timedelta(days=1)

    start_date = month_start.isoformat()
    end_date = next_month.isoformat()

    # Get employees who joined on or before the month's last day.
    pipeline = [
        {
            "$match": {
                "joined_on": {"$lte": month_end.isoformat()}
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "employee_codes": {"$push": "$emp_code"},
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"codes": "$employee_codes"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$emp_code", "$$codes"]},
                                    {"$gte": ["$date", start_date]},
                                    {"$lt": ["$date", end_date]},
                                ]
                            }
                        }
                    },
                    {
                        "$set": {
                            "is_present": {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            "attendance_day": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                    "timezone": "Asia/Kolkata",
                                }
                            },
                        }
                    },
                    {
                        "$set": {
                            "is_weekday": {
                                "$and": [
                                    {
                                        "$gte": [
                                            {"$dayOfWeek": "$attendance_day"},
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {"$dayOfWeek": "$attendance_day"},
                                            6,
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,

                            # Count weekday present days.
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$is_present",
                                                "$is_weekday",
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },

                            # Average only non-null work_hours
                            # for present-status records.
                            "avg_work_hours": {
                                "$avg": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$is_present",
                                                {"$ne": ["$work_hours", None]},
                                            ]
                                        },
                                        "$work_hours",
                                        None,
                                    ]
                                }
                            },

                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {"$ifNull": ["$late_minutes", 0]},
                                                0,
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },

                            "total_late_minutes": {
                                "$sum": {"$ifNull": ["$late_minutes", 0]}
                            },

                            "leave_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "LEAVE"]},
                                        1,
                                        0,
                                    ]
                                }
                            },

                            "on_duty_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "ON_DUTY"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "attendance_summary",
            }
        },
        {
            "$set": {
                "attendance_summary": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$attendance_summary", 0]},
                        {
                            "present_days": 0,
                            "avg_work_hours": None,
                            "late_count": 0,
                            "total_late_minutes": 0,
                            "leave_count": 0,
                            "on_duty_count": 0,
                        },
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": "$attendance_summary.present_days",
                "avg_work_hours": "$attendance_summary.avg_work_hours",
                "late_count": "$attendance_summary.late_count",
                "total_late_minutes": "$attendance_summary.total_late_minutes",
                "leave_count": "$attendance_summary.leave_count",
                "on_duty_count": "$attendance_summary.on_duty_count",
            }
        },
        {
            "$sort": {"department": 1}
        },
    ]

    result = list(db.employees.aggregate(pipeline))

    # Return the response in the required format.
    return {
        "month": month,
        "items": result,
    }


# --------------------------------------------------------------------------- #
# Late leaderboard

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    # Validate the month.
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Month must use YYYY-MM format",
        )

    # Calculate the next month's starting date.
    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month = month_start.replace(
            month=month_start.month + 1,
        )

    start_date = month_start.isoformat()
    end_date = next_month.isoformat()

    # Build the aggregation pipeline.
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start_date,
                    "$lt": end_date,
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {"$sum": 1},
            }
        },
        # Ignore employees who do not have a matching employee record.
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {
            "$unwind": "$employee"
        },
    ]

    # Apply the optional department filter before calculating ranks.
    if department:
        pipeline.append(
            {
                "$match": {
                    "employee.department": department,
                }
            }
        )

    pipeline.extend(
        [
            # Competition ranking: ties receive the same rank.
            {
                "$setWindowFields": {
                    "sortBy": {
                        "total_late_minutes": -1,
                    },
                    "output": {
                        "rank": {"$rank": {}},
                    },
                }
            },
            # Use emp_code as a stable secondary sort.
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "_id": 1,
                }
            },
            # Keep all employees whose rank is within the requested limit.
            {
                "$match": {
                    "rank": {"$lte": limit},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ]
    )

    result = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": result,
    }



#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
