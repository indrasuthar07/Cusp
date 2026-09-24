-- Token bucket rate limiter.

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local fill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local bucket = redis.call("HMGET", key, "tokens", "last_update")
local tokens = tonumber(bucket[1])
local last_update = tonumber(bucket[2])

if not tokens then
    tokens = capacity
    last_update = now
end

local elapsed = math.max(0, now - last_update)
tokens = math.min(capacity, tokens + (elapsed * fill_rate))

if tokens >= requested then
    tokens = tokens - requested
    redis.call("HMSET", key, "tokens", tokens, "last_update", now)
    redis.call("EXPIRE", key, math.ceil(capacity / fill_rate))
    return {ok = "allowed"}
else
    return {err = "Rate limit exceeded."}
end