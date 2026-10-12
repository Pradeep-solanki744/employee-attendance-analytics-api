
# REVIEW.md

## Defects Found in the Starter Code

| # | Where                   | What is wrong                                                                                                      | How to notice                                                                                                    | How it was fixed                                                                            |
| - | ----------------------- | ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 1 | Employee creation       | Duplicate employee codes must not create duplicate employees.                                                      | Submit two employees with the same `emp_code`.                                                                   | Added a unique index on `employees.emp_code` and handled `DuplicateKeyError` with HTTP 409. |
| 2 | Attendance punch-in     | Repeated punch-in requests must not create multiple records for the same employee and date.                        | Submit punch-in twice for the same employee on the same attendance date.                                         | Added a unique compound index on `emp_code` and `date`; duplicate requests return HTTP 409. |
| 3 | Attendance punch-out    | A punch-out must not overwrite a record that has already been punched out.                                         | Submit two punch-out requests for the same open attendance record.                                               | Use an atomic update that matches only records whose `punch_out` is still null.             |
| 4 | Attendance calculations | Work hours, late minutes, overtime, and half-day status must follow the assignment's rounding and threshold rules. | Test boundary cases such as exactly 10 minutes late, exactly 30 minutes of overtime, and work hours around 4.50. | Apply the specified thresholds and rounding rules in the calculation helpers.               |
| 5 | Department summary      | Employees without attendance logs must still count toward department headcount.                                    | Include an employee who joined before the reporting period ends but has no attendance records.                   | Start the aggregation from employees and look up attendance logs afterward.                 |

## Reviewed but Not Considered Defects

* Missing attendance records are not automatically ABSENT; the assignment distinguishes no record from an explicit ABSENT record.
* Missing `half_day` values default to `false`, as required by the data model.
* The attendance date is based on Indian Standard Time (IST), rather than the server's local timezone.
* The leaderboard gives tied employees the same competition rank. Results may exceed the requested limit when several employees tie at the cutoff.
* Creating indexes at startup is appropriate when index creation is idempotent and the existing data satisfies any unique-index constraints.
