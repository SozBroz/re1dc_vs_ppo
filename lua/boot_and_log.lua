--[[
  boot_and_log.lua -- autonomous ROOM_ID hunt.

  Boots RE1 from power-on, mashes Start/Cross to skip the FMV and enter a new
  game (default highlighted options), and logs the work-RAM block throughout.
  RE1's intro scripts the party through several rooms via cutscene, so the room
  register should change on its own -- letting us catch it with no human input.

  Writes data/ram_log.csv (byte block) + data/boot_log.txt (progress).
  Self-exits after MAX_FRAMES so it never hangs the machine.
]]

local START = 0x0C8000
local COUNT = 0x900
local LOG_EVERY = 15
local MAX_FRAMES = 9000        -- ~2.5 min of emulated time
local OUT = "D:/re1_rl/data/ram_log.csv"
local LOG = "D:/re1_rl/data/boot_log.txt"

local prog = io.open(LOG, "w")
prog:write("system=" .. emu.getsystemid() .. "\n")
prog:flush()

local f = io.open(OUT, "w")
f:write("frame")
for i = 0, COUNT - 1 do f:write(string.format(",b%X", START + i)) end
f:write("\n")

local function sample()
    f:write(tostring(emu.framecount()))
    for i = 0, COUNT - 1 do
        f:write("," .. memory.readbyte(START + i, "MainRAM"))
    end
    f:write("\n")
    f:flush()
end

client.speedmode(1600)

local n = 0
local last_hp = -1
while emu.framecount() < MAX_FRAMES do
    -- mash confirm/start on alternating frames to advance menus & text
    if n % 4 == 0 then
        joypad.set({ ["P1 Cross"] = true })
    elseif n % 4 == 2 then
        joypad.set({ ["P1 Start"] = true })
    else
        joypad.set({})
    end

    if n % LOG_EVERY == 0 then
        sample()
        local hp = memory.read_u16_le(0x0C51AC, "MainRAM")
        if hp ~= last_hp then
            prog:write(string.format("frame %d hp=%d\n", emu.framecount(), hp))
            prog:flush()
            last_hp = hp
        end
    end
    n = n + 1
    emu.frameadvance()
end

prog:write("DONE frames=" .. emu.framecount() .. "\n")
prog:close()
f:close()
client.speedmode(100)
client.exitprogram()
