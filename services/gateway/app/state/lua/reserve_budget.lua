-- Reserve budget for a single action atomically.

local run_key = KEYS[1]
local idem_key = KEYS[2]
local org_key = KEYS[3]
local task_key = KEYS[4]
local reservation_key = KEYS[5]

local idem_val = ARGV[1]
local est_cost = tonumber(ARGV[2])
local run_cap = tonumber(ARGV[3])
local org_cap = tonumber(ARGV[4])
local task_cap = tonumber(ARGV[5])
local ts = ARGV[6]
local action_id = ARGV[7]

if not est_cost or est_cost < 0 or not run_cap or run_cap < 0 or not org_cap or org_cap < 0 then
    return {err = "Invalid budget values."}
end

-- 1. Existing reservations are idempotent and return without charging twice.
if redis.call("HGET", reservation_key, "status") == "reserved" then
    return {ok = "duplicate"}
end
if idem_val ~= "" and redis.call("EXISTS", idem_key) == 1 then
    return {ok = "duplicate"}
end

-- 2. Check Run State and Run Budget
local run_status = redis.call("HGET", run_key, "status")
if run_status == "paused" or run_status == "terminated" or run_status == "completed" or run_status == "failed" then
    return {err = "Run is in terminal or paused state: " .. (run_status or "unknown")}
end

local run_spent = tonumber(redis.call("HGET", run_key, "spent") or "0")
local run_reserved = tonumber(redis.call("HGET", run_key, "reserved") or "0")

if (run_spent + run_reserved + est_cost) > run_cap then
    return {err = "Run budget cap exceeded."}
end

-- 3. Check Org Budget
local org_spent = tonumber(redis.call("HGET", org_key, "spent") or "0")
local org_reserved = tonumber(redis.call("HGET", org_key, "reserved") or "0")

if (org_spent + org_reserved + est_cost) > org_cap then
    return {err = "Organization budget cap exceeded."}
end

-- 4. Check Task Group Budget (if applicable)
if task_key ~= "" then
    local task_spent = tonumber(redis.call("HGET", task_key, "spent") or "0")
    local task_reserved = tonumber(redis.call("HGET", task_key, "reserved") or "0")
    
    if (task_spent + task_reserved + est_cost) > task_cap then
        return {err = "Task group budget cap exceeded."}
    end
    redis.call("HINCRBY", task_key, "reserved", est_cost)
end

-- 5. Commit idempotency and reservation only after every rejection check passes.
if idem_val ~= "" then
    local locked = redis.call("SET", idem_key, action_id, "NX", "EX", 300)
    if not locked then
        return {ok = "duplicate"}
    end
end
redis.call("HSET", reservation_key, "status", "reserved", "estimated", est_cost, "action_id", action_id)
redis.call("EXPIRE", reservation_key, 3600)
redis.call("HINCRBY", run_key, "reserved", est_cost)
redis.call("HINCRBY", org_key, "reserved", est_cost)
redis.call("HSET", org_key, "updated_at", ts)

return {ok = "reserved"}