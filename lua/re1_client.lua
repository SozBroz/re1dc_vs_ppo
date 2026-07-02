--[[
  re1_client.lua — BizHawk Lua client for Resident Evil 1 (SLUS-00170).

  Connects to Python BizHawkClient TCP server (comm.socketServer*).
  Pattern follows BrainHawk / GymBizHawk length-prefixed JSON RPC.

  Load: Tools → Lua Console → Script → lua/re1_client.lua
  TODO: Confirm comm.socketServerScreenShot() availability on your BizHawk build.
]]

local SERVER_HOST = "127.0.0.1"
local SERVER_PORT = 5555

local function encode_message(payload)
    return tostring(#payload) .. " " .. payload
end

local function decode_message(sock)
    local len_str = ""
    while true do
        local ch = sock:receive(1)
        if ch == " " then break end
        len_str = len_str .. ch
    end
    local len = tonumber(len_str)
    return sock:receive(len)
end

local function ps1_to_mainram(addr)
    return addr - 0x80000000
end

local function read_field(addr, dtype)
    local off = ps1_to_mainram(addr)
    if dtype == "u16" then
        return memory.read_u16_le(off, "MainRAM")
    elseif dtype == "u32" then
        return memory.read_u32_le(off, "MainRAM")
    elseif dtype == "u8" then
        return memory.readbyte(off, "MainRAM")
    else
        error("unsupported dtype: " .. tostring(dtype))
    end
end

local PSX_BUTTONS = {
    "P1 Up", "P1 Down", "P1 Left", "P1 Right",
    "P1 Cross", "P1 Square", "P1 Triangle", "P1 Circle", "P1 R1",
}

local function buttons_from_dict(btn)
    local out = {}
    for _, name in ipairs(PSX_BUTTONS) do
        out[name] = btn[name] == true
    end
    return out
end

local function handle_command(cmd)
    local op = cmd.cmd

    if op == "read_ram" then
        local values = {}
        for _, field in ipairs(cmd.fields) do
            local name, addr, dtype = field[1], field[2], field[3]
            values[name] = read_field(addr, dtype)
        end
        return { ok = true, values = values }

    elseif op == "buttons" then
        joypad.set(buttons_from_dict(cmd.buttons))
        return { ok = true }

    elseif op == "frameadvance" then
        local n = cmd.n or 1
        for _ = 1, n do
            emu.frameadvance()
        end
        return { ok = true }

    elseif op == "loadstate" then
        -- TODO: verify path conventions (absolute vs BizHawk state dir)
        savestate.load(cmd.path)
        return { ok = true }

    elseif op == "screenshot" then
        -- comm.socketServerScreenShot returns base64 PNG when available
        local png_b64 = ""
        if comm.socketServerScreenShot then
            png_b64 = comm.socketServerScreenShot()
        else
            -- Fallback: raw framebuffer encode not implemented yet
            console.log("WARN: socketServerScreenShot unavailable")
        end
        return { ok = true, png_b64 = png_b64 }

    else
        return { ok = false, error = "unknown cmd: " .. tostring(op) }
    end
end

-- ---------------------------------------------------------------------------
-- Main loop
-- ---------------------------------------------------------------------------

console.log("re1_client: connecting to " .. SERVER_HOST .. ":" .. SERVER_PORT)
local server = comm.socketServer(SERVER_PORT)
server:settimeout(0.5)

while true do
    local ok, payload = pcall(function()
        return decode_message(server)
    end)

    if ok and payload then
        local cmd_ok, cmd = pcall(function()
            return json.decode(payload)
        end)
        if not cmd_ok then
            server:send(encode_message('{"ok":false,"error":"bad json"}'))
        else
            local resp_ok, resp = pcall(handle_command, cmd)
            if resp_ok then
                server:send(encode_message(json.encode(resp)))
            else
                server:send(encode_message(json.encode({ ok = false, error = tostring(resp) })))
            end
        end
    end

    emu.frameadvance()
end
