-- indexes_access_logs.sql : the three access_logs indexes. Included by indexes.sql and by
-- explain_plan_demo.sql ("after" phase) so both always use identical definitions.
-- STATUS: NOT EXECUTED
CREATE INDEX ix_logs_file_time   ON access_logs (file_id, access_time);
CREATE INDEX ix_logs_user_time   ON access_logs (user_id, access_time);
CREATE INDEX ix_logs_region_time ON access_logs (served_by_region_id, access_time);
