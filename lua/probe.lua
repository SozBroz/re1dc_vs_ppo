-- Boot probe: verify core loads, RAM domain readable, measure fast-forward speed.
local out = io.open("D:/re1_rl/data/bizhawk_probe.txt", "w")

out:write("system: " .. emu.getsystemid() .. "\n")

-- warm up through BIOS
for i = 1, 120 do emu.frameadvance() end

client.speedmode(3200)
local t0 = os.clock()
local n = 1200
for i = 1, n do emu.frameadvance() end
local dt = os.clock() - t0
client.speedmode(100)

out:write(string.format("frames: %d\n", emu.framecount()))
out:write(string.format("ff_frames: %d in %.2fs -> %.1f fps (%.1fx realtime)\n",
    n, dt, n / dt, (n / dt) / 60.0))
out:write(string.format("hp_u16@0C51AC: %d\n", memory.read_u16_le(0x0C51AC, "MainRAM")))
out:write(string.format("timer_u32@0C867C: %d\n", memory.read_u32_le(0x0C867C, "MainRAM")))
out:write("PROBE_DONE\n")
out:close()
client.exitprogram()
