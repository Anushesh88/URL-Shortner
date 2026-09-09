-- KEYS[1] = sliding window key (e.g. rate_limit:sliding:<ip>)
-- ARGV[1] = current timestamp in milliseconds
-- ARGV[2] = window size in milliseconds
-- ARGV[3] = max allowed requests in window

local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local clear_before = now - window

-- 1. Remove old timestamps outside the window
redis.call("ZREMRANGEBYSCORE", KEYS[1], "-inf", clear_before)

-- 2. Count requests currently in the window
local current_requests = redis.call("ZCARD", KEYS[1])

if current_requests < max_requests then
    -- 3. Add current request timestamp (using now as both score and member for uniqueness, or now:uuid)
    redis.call("ZADD", KEYS[1], now, tostring(now) .. ":" .. tostring(math.random(100000, 999999)))
    redis.call("PEXPIRE", KEYS[1], window)
    return {1, max_requests - current_requests - 1}
else
    return {0, 0}
end
