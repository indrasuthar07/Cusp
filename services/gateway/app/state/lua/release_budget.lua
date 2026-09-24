-- Reconcile budget after action completes.

local run_key = KEYS[1]
local org_key = KEYS[2]
local task_key = KEYS[3]

local est_cost = tonumber(ARGV[1])
local act_cost = tonumber(ARGV[2])
local ts = ARGV[3]

-- Run update
redis.call("HINCRBY", run_key, "reserved", -est_cost)
redis.call("HINCRBY", run_key, "spent", act_cost)

-- Org update
redis.call("HINCRBY", org_key, "reserved", -est_cost)
redis.call("HINCRBY", org_key, "spent", act_cost)
redis.call("HSET", org_key, "updated_at", ts)

-- Task group update (if applicable)
if task_key ~= "" then
    redis.call("HINCRBY", task_key, "reserved", -est_cost)
    redis.call("HINCRBY", task_key, "spent", act_cost)
end

return {ok = "released"}
