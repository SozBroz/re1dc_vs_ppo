--[[
  room_hunt.lua -- targeted ROOM_ID confirmation pass.

  Logs two candidate blocks every 10 frames:
    A) 0x0C9800-0x0C99FF  (predicted stage/room bytes at 0xC9880/0xC9884)
    B) 0x0C8440-0x0C84A0  (candidates from first unsupervised pass)

  Phase 1 (frames 0-3000): mash Cross/Start through FMV + menus.
  Phase 2 (3000+): crude random walk -- hold Up to move, occasional turns,
  periodic Cross presses to open any door we bump into. Goal: at least one
  genuine room transition with no human input.
]]

local OUT = "D:/re1_rl/data/room_hunt.csv"
local LOG = "D:/re1_rl/data/room_hunt_log.txt"
local MAX_FRAMES = 30000

local BLOCKS = {
    { start = 0x0C9800, count = 0x200 },
    { start = 0x0C8440, count = 0x60 },
}

local prog = io.open(LOG, "w")
prog:write("system=" .. emu.getsystemid() .. "\n")
prog:flush()

local f = io.open(OUT, "w")
f:write("frame")
for _, blk in ipairs(BLOCKS) do
    for i = 0, blk.count - 1 do f:write(string.format(",b%X", blk.start + i)) end
end
f:write("\n")

local function sample()
    f:write(tostring(emu.framecount()))
    for _, blk in ipairs(BLOCKS) do
        for i = 0, blk.count - 1 do
            f:write("," .. memory.readbyte(blk.start + i, "MainRAM"))
        end
    end
    f:write("\n")
end

client.speedmode(1600)
math.randomseed(12345)

local n = 0
local last_hp = -1
local walk_dir = nil
local walk_left = 0

while emu.framecount() < MAX_FRAMES do
    local btn = {}
    if n < 3000 then
        if n % 4 == 0 then btn["P1 Cross"] = true
        elseif n % 4 == 2 then btn["P1 Start"] = true end
    else
        if walk_left <= 0 then
            local r = math.random()
            if r < 0.55 then walk_dir = "up"; walk_left = 90
            elseif r < 0.75 then walk_dir = "left"; walk_left = 25
            elseif r < 0.95 then walk_dir = "right"; walk_left = 25
            else walk_dir = "cross"; walk_left = 4 end
        end
        if walk_dir == "up" then btn["P1 Up"] = true
        elseif walk_dir == "left" then btn["P1 Left"] = true
        elseif walk_dir == "right" then btn["P1 Right"] = true
        elseif walk_dir == "cross" then btn["P1 Cross"] = true end
        -- press Cross briefly at the end of every forward walk (door attempt)
        if walk_dir == "up" and walk_left <= 3 then btn["P1 Cross"] = true end
        walk_left = walk_left - 1
    end
    joypad.set(btn)

    if n % 10 == 0 then
        sample()
        local hp = memory.read_u16_le(0x0C51AC, "MainRAM")
        if hp ~= last_hp then
            prog:write(string.format("frame %d hp=%d stage=%d room=%d\n",
                emu.framecount(), hp,
                memory.readbyte(0x0C9880, "MainRAM"),
                memory.readbyte(0x0C9884, "MainRAM")))
            prog:flush()
            last_hp = hp
        end
    end
    if n % 600 == 0 then f:flush() end
    n = n + 1
    emu.frameadvance()
end

prog:write("DONE frames=" .. emu.framecount() .. "\n")
prog:close()
f:close()
client.speedmode(100)
client.exitprogram()
