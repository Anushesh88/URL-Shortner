-- KEYS[1] = bucket key
-- ARGV[1] = capacity (max tokens)
-- ARGV[2] = refill_rate (tokens per second)
-- ARGV[3] = current_time (Unix timestamp in float seconds)

local bucket = redis.call("HMGET", KEYS[1], "tokens", "last_refill")
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

-- Compute token refill based on elapsed wall-clock time
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

if tokens < 1 then
    -- Rate limit exceeded
    return {0, math.floor(tokens)}
else
    -- Consume 1 token
    tokens = tokens - 1
    redis.call("HMSET", KEYS[1], "tokens", tokens, "last_refill", now)
    redis.call("EXPIRE", KEYS[1], 3600)
    return {1, math.floor(tokens)}
end
