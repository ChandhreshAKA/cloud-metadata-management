# Chunk 1: EXPLAIN PLAN results

**NOT EXECUTED.** Paste real output into each section after running `sql/explain_plan_demo.sql` (see `01-run-guide.md`, step 5). Leave nothing here that did not come from your database.

Record environment once:

| Item | Value |
|---|---|
| Oracle version (`SELECT banner FROM v$version`) | |
| SQL*Plus version | |
| access_logs rows | 10,010 / 100,010 |
| Rows for the chosen file_id / user_id (printed by script) | |

## Summary (fill in after pasting)

| Query | Size | Phase | Plan operation on access_logs | Estimated cost | Actual buffers | Actual A-Time | Sort step? |
|---|---|---|---|---|---|---|---|
| Q2 file history | 10k | before | | | | | |
| Q2 file history | 10k | after | | | | | |
| Q2 file history | 100k | before | | | | | |
| Q2 file history | 100k | after | | | | | |
| Q3 user top 50 | 10k | before | | | | | |
| Q3 user top 50 | 10k | after | | | | | |
| Q3 user top 50 | 100k | before | | | | | |
| Q3 user top 50 | 100k | after | | | | | |
| Q4 control | 10k | before | | | | | |
| Q4 control | 10k | after | | | | | |
| Q4 control | 100k | before | | | | | |
| Q4 control | 100k | after | | | | | |

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

## Interpretation (I will write this after you paste the output)

