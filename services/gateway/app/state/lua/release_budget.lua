-- Reconcile budget after action completes.

local run_key = KEYS[1]
local org_key = KEYS[2]
local task_key = KEYS[3]
local reservation_key = KEYS[4]

local act_cost = tonumber(ARGV[1])
local ts = ARGV[2]
local action_id = ARGV[3]

local reservation_status = redis.call("HGET", reservation_key, "status")
if reservation_status == "released" then
    return {ok = "already_released"}
end
if reservation_status ~= "reserved" then
    return {err = "Unknown or already released action reservation."}
end
local est_cost = tonumber(redis.call("HGET", reservation_key, "estimated") or "0")
if est_cost < 0 or act_cost < 0 then
    return {err = "Negative cost is invalid."}
end
if redis.call("HGET", reservation_key, "action_id") ~= action_id then
    return {err = "Action reservation mismatch."}
end

-- Run update
redis.call("HINCRBY", run_key, "reserved", -est_cost)
redis.call("HINCRBY", run_key, "spent", act_cost)
local run_spent_after = tonumber(redis.call("HGET", run_key, "spent") or "0")
local run_cap = tonumber(redis.call("HGET", run_key, "cap") or "0")
local overrun = ""
if run_cap >= 0 and run_spent_after > run_cap then
    overrun = "overrun"
    redis.call("HSET", run_key, "status", "failed")
end

-- Org update
redis.call("HINCRBY", org_key, "reserved", -est_cost)
redis.call("HINCRBY", org_key, "spent", act_cost)
redis.call("HSET", org_key, "updated_at", ts)

-- Task group update (if applicable)
if task_key ~= "" then
    redis.call("HINCRBY", task_key, "reserved", -est_cost)
    redis.call("HINCRBY", task_key, "spent", act_cost)
end

redis.call("HSET", reservation_key, "status", "released", "actual", act_cost)
redis.call("EXPIRE", reservation_key, 3600)

return {"released", overrun}