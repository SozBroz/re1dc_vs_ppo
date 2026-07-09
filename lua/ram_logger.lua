--[[
  ram_logger.lua -- dump the RE1 work-RAM block to CSV every N frames.

  Purpose: find ROOM_ID (and other unknowns) by diffing the block across
  known room transitions. Play through a door; the byte(s) that change to the
  new room's hex id are the room register. HP (0x0C51AC) and timer (0x0C867C)
  are inside this window and serve as anchors to confirm alignment.

  Usage: load the game, open Tools -> Lua Console -> this script, then PLAY.
  Every LOG_EVERY frames a row is appended. After walking through several
  rooms, run scripts/find_room_id.py on the CSV.
]]

local START = 0x0C8000   -- MainRAM offset (PS1 bus 0x800C8000)
local COUNT = 0x900      -- covers 0x0C8000..0x0C88FF (save/work block)
local LOG_EVERY = 15     -- frames between samples (~4 Hz)
local OUT = "D:/re1_rl/data/ram_log.csv"

local f = io.open(OUT, "w")
-- header: frame + one column per byte offset
f:write("frame")
for i = 0, COUNT - 1 do
    f:write(string.format(",b%X", START + i))
end
f:write("\n")

local function sample()
    f:write(tostring(emu.framecount()))
    for i = 0, COUNT - 1 do
        f:write("," .. memory.readbyte(START + i, "MainRAM"))
    end
    f:write("\n")
    f:flush()
end

console.log("ram_logger: writing " .. OUT .. " -- play through rooms now")

local n = 0
while true do
    if n % LOG_EVERY == 0 then sample() end
    n = n + 1
    emu.frameadvance()
end
