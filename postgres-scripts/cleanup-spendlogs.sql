-- LiteLLM spend log cleanup — retains 90 days of records
-- Run weekly by Ofelia (litellm-spend-cleanup job)
-- Safe: no-op if no rows qualify; uses existing startTime index
DELETE FROM "LiteLLM_SpendLogs"
WHERE "startTime" < NOW() - INTERVAL '90 days';
