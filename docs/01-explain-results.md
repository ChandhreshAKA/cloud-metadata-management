# Chunk 1: EXPLAIN PLAN results

**100,010-row run EXECUTED (12:10, log `sql/explain_100k.log`). 10,010-row run NOT EXECUTED yet.** Paste real output into each section after running `sql/explain_plan_demo.sql` (see `01-run-guide.md`, step 5). Leave nothing here that did not come from your database.

**Chunks 6 and 7 added no index and changed no schema**, so the plans below are unaffected. The two report queries added in
chunk 6 have no recorded plan yet: `sql/explain_reports.sql` captures them (see `05-test-report.md`, section 2.13 and table T13).
NOT EXECUTED.

Record environment once:

| Item | Value |
|---|---|
| Oracle version | Oracle AI Database 26ai Free 23.26.3.0.0 (from the SQL*Plus banner) |
| SQL*Plus version | 23.26.3.0.0 |
| access_logs rows | 100,010 for the executed run (5,006 files, 103 users) |
| Rows for the chosen file_id / user_id (printed by script) | file_id 34 has 18 log rows; user_id 41 has 1,027 |

## Summary (fill in after pasting)

| Query | Size | Phase | Plan operation on access_logs | Estimated cost | Actual buffers | Actual A-Time | Sort step? |
|---|---|---|---|---|---|---|---|
| Q2 file history | 10k | before | | | | | |
| Q2 file history | 10k | after | | | | | |
| Q2 file history | 100k | before | TABLE ACCESS FULL | 207 | 752 | 00:00:00.01 | yes (SORT ORDER BY) |
| Q2 file history | 100k | after | INDEX RANGE SCAN DESCENDING IX_LOGS_FILE_TIME + TABLE ACCESS BY INDEX ROWID | 22 | 21 | 00:00:00.01 | no |
| Q3 user top 50 | 10k | before | | | | | |
| Q3 user top 50 | 10k | after | | | | | |
| Q3 user top 50 | 100k | before | TABLE ACCESS FULL | 207 | 752 | 00:00:00.01 | yes (SORT ORDER BY STOPKEY) |
| Q3 user top 50 | 100k | after | INDEX RANGE SCAN DESCENDING IX_LOGS_USER_TIME + TABLE ACCESS BY INDEX ROWID | 52 | 53 | 00:00:00.01 | no (COUNT STOPKEY only) |
| Q4 control | 10k | before | | | | | |
| Q4 control | 10k | after | | | | | |
| Q4 control | 100k | before | TABLE ACCESS FULL | 212 (whole statement) | 752 | 00:00:00.03 | no (HASH GROUP BY) |
| Q4 control | 100k | after | TABLE ACCESS FULL (unchanged) | 212 | 752 | 00:00:00.02 | no (HASH GROUP BY) |

## 10,000-row run

### Q2 BEFORE
```
(paste estimated plan, then ALLSTATS LAST plan)
```
### Q2 AFTER
```
```
### Q3 BEFORE
```
```
### Q3 AFTER
```
```
### Q4 BEFORE (control)
```
```
### Q4 AFTER (control)
```
```

## 100,000-row run

### Q2 BEFORE
```
```
### Q2 AFTER
```
```
### Q3 BEFORE
```
```
### Q3 AFTER
```
```
### Q4 BEFORE (control)
```
```
### Q4 AFTER (control)
```
```

## Interpretation

**100,010-row run (from `sql/explain_100k.log`; all numbers are copied from that log).**

- **Q2 (history of one file, 18 matching rows of 100,010):** the optimizer read the whole table before (752 buffer gets, estimated cost 207, plus a sort) and, after `ix_logs_file_time`, used an index range scan read backwards with no sort step: 21 buffer gets, estimated cost 22. That is about 36 times fewer logical reads. The index scan itself cost 3 buffers; the other 18 were table lookups for the 18 rows.
- **Q3 (latest 50 of one user, 1,027 matching rows):** before, a full scan fed a sort that kept the top 50 (752 buffers, cost 207). After `ix_logs_user_time`, a descending index scan stopped as soon as 50 rows were found (53 buffers, cost 52). About 14 times fewer logical reads. The table was read for 50 rows instead of 1,027 because of the stop key.
- **Q4 (control, 76,098 of 100,010 rows match):** the plan is identical before and after (full scan, same cost and buffers). This is the expected result: the query needs most of the table, and none of the three indexes contains `action` and `outcome`. It shows that an index does not help every query.
- Elapsed times were all about 0.01 to 0.03 seconds and are too small to be meaningful at this size. The buffer gets and the plan operations are the reliable evidence.
- Estimated and actual rows agreed closely (for example Q2: estimated 20, actual 18; Q3: 1,027 estimated and 1,027 read).

**10,010-row run:** NOT EXECUTED yet.

**Caveat:** one run on one machine, one file and one user chosen by the script. The buffer counts depend on those choices and on the random data, so they should not be quoted as general speedups.

