# Design Decisions

1. **Indexes:** I created a unique index on `employees.emp_code` so employee codes cannot be duplicated. A unique compound index on `attendance_logs.emp_code` and `date` allows only one attendance record per employee per day. I also added indexes for attendance date sorting and employee department queries. I would consider more indexes only after checking actual query plans, because extra indexes increase write and storage costs.

2. **Punch-in race:** If two identical punch-in requests arrive together, both may initially check the employee, but the unique attendance index allows only one record for the same employee and date. The successful request returns 201; the other catches `DuplicateKeyError` and returns 409.

3. **Ties:** The late leaderboard uses MongoDB `$rank`, so employees with equal total late minutes receive the same rank. Results are then sorted by late minutes and employee code for stable ordering. The query keeps every employee whose rank is within `limit`, so ties at the cutoff can make the result contain more than `limit` employees.

4. **Headcount:** Department summary starts from the employees collection and groups employees by department before looking up attendance logs. This means employees who have no attendance records can still contribute to headcount.

5. **100x data:** I would inspect query plans and performance first, then tune indexes for the most common filters. I would also review aggregation memory and execution time, and consider pagination or precomputed monthly summaries if analytics became too expensive.
