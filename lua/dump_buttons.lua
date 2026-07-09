-- Dump joypad button names (with byte escapes for non-ASCII), then exit.
local f = io.open("D:/re1_rl/data/button_names.txt", "w")
emu.frameadvance()
local t = joypad.get()
for k, v in pairs(t) do
    local esc = k:gsub("[\128-\255]", function(c) return string.format("\\x%02X", c:byte()) end)
    f:write(esc .. " = " .. tostring(v) .. "\n")
end
f:close()
client.exitprogram()
