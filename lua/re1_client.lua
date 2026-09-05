--[[
  re1_client.lua -- BizHawk Lua client for Resident Evil 1 (SLUS-00551).

  Uses BizHawk's BUILT-IN comm socket API (BizHawk bundles no luasocket).
  EmuHawk must be launched with:
      EmuHawk.exe <rom> --lua=lua/re1_client.lua --socket_ip=127.0.0.1 --socket_port=5555
  and the Python BizHawkClient server must already be listening.

  Wire format (both directions): length-prefixed UTF-8 -> "{len} {payload}",
  payload is JSON. comm.socketServerSend() adds the prefix automatically
  (BizHawk >= 2.6.2); comm.socketServerResponse() strips it on receive.

  Flow: Lua sends {"hello": ...} once, then loops:
      cmd = socketServerResponse()  (blocking)  ->  execute  ->  send result.

  Screenshots are written to a PNG file via client.screenshot(path); Python
  reads the file (avoids binary-over-socket issues).
]]

-- BizHawk has no built-in JSON; use bundled dkjson.lua (same dir as this script).
-- Resolve SCRIPT_DIR without hardcoding a drive letter: WH2 has no D: (repo under
-- C:\Users\sshuser\re1_rl); WH1/pking use D:\re1_rl. Prefer this file's path;
-- fall back to known install roots if debug.getinfo is unhelpful.
local function script_dir_candidates()
  local dirs = {}
  local function add(dir)
    if type(dir) == "string" and #dir > 0 then
      if dir:sub(-1) ~= "/" and dir:sub(-1) ~= "\\" then
        dir = dir .. "/"
      end
      dirs[#dirs + 1] = dir
    end
  end
  local src = debug.getinfo(1, "S").source
  if type(src) == "string" and src:sub(1, 1) == "@" then
    src = src:sub(2)
  end
  if type(src) == "string" then
    add(src:match("^(.*[/\\])"))
  end
  add("C:/Users/sshuser/re1_rl/lua/")
  add("D:/re1_rl/lua/")
  add("./lua/")
  add("./")
  return dirs
end

local SCRIPT_DIR = "./"
local json
do
  local last_err = "dkjson not found"
  for _, dir in ipairs(script_dir_candidates()) do
    -- Direct loading avoids WH2's package.search/require hang before hello.
    local ok, mod = pcall(dofile, dir .. "dkjson.lua")
    if ok and mod then
      SCRIPT_DIR = dir
      json = mod
      break
    end
    last_err = tostring(mod)
  end
  if not json then
    error("re1_client: cannot load dkjson.lua (" .. last_err .. ")")
  end
end

-- Default only; Python usually passes an explicit screenshot path per port.
local SHOT_PATH = SCRIPT_DIR .. "../data/_frame.png"

local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

local function base64_encode(data)
    return ((data:gsub(".", function(x)
        local r, bits = "", x:byte()
        for i = 8, 1, -1 do
            r = r .. (bits % 2 ^ i - bits % 2 ^ (i - 1) > 0 and "1" or "0")
        end
        return r
    end) .. "0000"):gsub("%d%d%d?%d?%d?%d?%d?%d?", function(x)
        if #x < 6 then
            return ""
        end
        local c = 0
        for i = 1, 6 do
            c = c + (x:sub(i, i) == "1" and 2 ^ (6 - i) or 0)
        end
        return B64:sub(c + 1, c + 1)
    end) .. ({ "", "==", "=" })[#data % 3 + 1])
end

local function mmf_capture(mmf_name)
    if comm.mmfSetFilename then
        comm.mmfSetFilename(mmf_name)
    end
    if not comm.mmfScreenshot then
        return nil, 0, "mmfScreenshot unavailable"
    end
    local size = tonumber(comm.mmfScreenshot()) or 0
    if size <= 0 then
        return nil, 0, "mmfScreenshot returned size 0"
    end
    local got = comm.mmfGetFilename and comm.mmfGetFilename() or mmf_name
    return got, size, nil
end

do
  local marker = SCRIPT_DIR .. "../data/logs/_lua_script_dir.txt"
  local f = io.open(marker, "w")
  if f then
    f:write(SCRIPT_DIR)
    f:close()
  end
end
if console and console.log then
  console.log("re1_client: SCRIPT_DIR=" .. SCRIPT_DIR)
end

local function mmf_png_b64(mmf_name)
    local got, size, err = mmf_capture(mmf_name)
    if err or not got or size <= 0 then
        return nil, nil
    end
    local png_bytes = ""
    if comm.mmfReadBytes then
        local bytes = comm.mmfReadBytes(got, size)
        if type(bytes) == "table" then
            local parts = {}
            for i = 0, size - 1 do
                local b = bytes[i]
                if b == nil then
                    break
                end
                parts[#parts + 1] = string.char(b)
            end
            png_bytes = table.concat(parts)
        end
    elseif comm.mmfRead then
        png_bytes = comm.mmfRead(got, size) or ""
    end
    if png_bytes == nil or #png_bytes == 0 then
        return nil, nil
    end
    return emu.framecount(), base64_encode(png_bytes)
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
    elseif dtype == "s16" then
        return memory.read_s16_le(off, "MainRAM")
    else
        error("unsupported dtype: " .. tostring(dtype))
    end
end

local function write_field(addr, dtype, value)
    local off = ps1_to_mainram(addr)
    local v = tonumber(value)
    if dtype == "u16" then
        memory.write_u16_le(off, v, "MainRAM")
    elseif dtype == "u32" then
        memory.write_u32_le(off, v, "MainRAM")
    elseif dtype == "u8" then
        memory.writebyte(off, v, "MainRAM")
    elseif dtype == "s16" then
        memory.write_s16_le(off, v, "MainRAM")
    else
        error("unsupported dtype: " .. tostring(dtype))
    end
end

-- When hp_floor > 0, a 0-HP read from a poison-sized chip is rewritten
-- instead of aborting. Zombie/bite drops (last live HP > chip_max) still die.
-- Returns saw_positive_hp, dead, last_hp.
local function apply_hp_floor_check(
    hp_off, hp_floor, chip_max, abort_on_zero_hp, saw_positive_hp, last_hp
)
    if not hp_off then
        return saw_positive_hp, false, last_hp
    end
    local hp = memory.read_u16_le(hp_off, "MainRAM")
    local floor = tonumber(hp_floor) or 0
    chip_max = tonumber(chip_max) or 4
    last_hp = tonumber(last_hp) or 0
    if hp > 0 then
        return true, false, hp
    end
    if floor > 0 and last_hp > 0 and last_hp <= chip_max then
        memory.write_u16_le(hp_off, floor, "MainRAM")
        return saw_positive_hp, false, floor
    end
    if abort_on_zero_hp and saw_positive_hp then
        return saw_positive_hp, true, last_hp
    end
    return saw_positive_hp, false, last_hp
end

--[[
  GameShark-style engine patches, re-applied before EVERY frame advance
  (savestate loads revert MainRAM, and 8-type GameShark codes are defined as
  per-frame constant writes). Set from Python via the "set_patches" command:
    always: list of [addr, dtype, value] unconditional writes
    turbo:  {addr, on_value, off_value, mode_addr, mask} -- write on_value
            while (u8@mode_addr & mask) == 0 (cutscene), else off_value
]]
local PATCHES = { always = {}, turbo = nil }
local LAST_TURBO = false

-- force_turbo: write on_value regardless of the in-control bit (used by
-- fast_forward, which already guarantees we are inside an uncontrolled span).
local function apply_patches(force_turbo)
    for _, p in ipairs(PATCHES.always) do
        write_field(p[1], p[2], p[3])
    end
    local t = PATCHES.turbo
    if t then
        local mode = memory.readbyte(ps1_to_mainram(t.mode_addr), "MainRAM")
        -- (mode & mask) ~= 0 without the bit library (Lua 5.4 safe);
        -- mask is a power of two.
        local in_control = math.floor(mode / t.mask) % 2 == 1
        -- force_turbo == "off": never write the cutscene turbo halfword.
        -- 1x combat/grab tapes record scene_flag spans as policy holds;
        -- forcing turbo on replay shortens them and desyncs HP.
        local turbo_on
        if force_turbo == "off" then
            turbo_on = false
        elseif force_turbo then
            turbo_on = true
        else
            turbo_on = not in_control
        end
        if turbo_on then
            memory.write_u16_le(ps1_to_mainram(t.addr), t.on_value, "MainRAM")
        else
            memory.write_u16_le(ps1_to_mainram(t.addr), t.off_value, "MainRAM")
        end
        LAST_TURBO = turbo_on == true
    else
        LAST_TURBO = false
    end
end

-- Must match re1_rl.memory_map + ram_skip.scene_active / message_open.
-- Recording's fast_forward uses apply_patches(true) on these spans; tape_play
-- must do the same or in-control scripted scenes (bar examine, Kenneth) play
-- at 1x while the tape still holds only the turbo-length Cross mash.
local TAPE_GAME_MODE = 0x800C3003
local TAPE_IN_CONTROL_MASK = 0x80
local TAPE_MESSAGE_FLAG = 0x800C8665
local TAPE_MESSAGE_MASK = 0x80
local TAPE_SCENE_FLAG = 0x800C3002
local TAPE_SCENE_BIT = 0x10

local function tape_skip_force_turbo()
    local mode = memory.readbyte(ps1_to_mainram(TAPE_GAME_MODE), "MainRAM")
    local in_control = math.floor(mode / TAPE_IN_CONTROL_MASK) % 2 == 1
    local msg = memory.readbyte(ps1_to_mainram(TAPE_MESSAGE_FLAG), "MainRAM")
    local msg_open = math.floor(msg / TAPE_MESSAGE_MASK) % 2 == 1
    local scene = memory.readbyte(ps1_to_mainram(TAPE_SCENE_FLAG), "MainRAM")
    local scene_active = (math.floor(scene / TAPE_SCENE_BIT) % 2 == 1)
        or (math.floor(scene % 128) ~= 0)
    return (not in_control) or msg_open or scene_active
end

-- client.invisibleemulation is absent in some BizHawk 2.11 builds; degrade to
-- rendering the fast-forward rather than crashing the whole client loop.
local function set_invisible(on)
    local f = client.invisibleemulation or client.InvisibleEmulation
    if f then
        pcall(f, on == true)
    end
end

-- Buttons rotated while fast-forwarding uncontrolled spans (dialogue advance,
-- door prompts, FMV/Start skip). 2 frames held, 2 released (30fps game logic).
local FF_MASH = {
    { cross = true },
    { triangle = true },
    { start = true },
    { cross = true, triangle = true },
    { circle = true },
    { square = true },
}

-- Friendly name -> Nymashock core button name (verified via joypad.get() dump).
-- Face buttons use unicode glyphs in the core: X, triangle, square, circle.
local BUTTON_MAP = {
    up = "P1 D-Pad Up",
    down = "P1 D-Pad Down",
    left = "P1 D-Pad Left",
    right = "P1 D-Pad Right",
    cross = "P1 X",
    triangle = "P1 \226\150\179",  -- △
    square = "P1 \226\150\161",    -- □
    circle = "P1 \226\151\139",    -- ○
    start = "P1 Start",
    select = "P1 Select",
    r1 = "P1 R1",
    l1 = "P1 L1",
    r2 = "P1 R2",
    l2 = "P1 L2",
}

-- TAS-grade per-frame joypad tape (YouTube / pixel-perfect replay).
-- Bit 0 = up, then down, left, right, cross, triangle, square, circle,
-- start, select, r1, l1, r2, l2; bit 14 records cutscene turbo. Each 15-bit
-- word is stored as three JSON-safe base64 alphabet characters so long legs do
-- not retain Lua number tables or pass binary NULs through BizHawk's Lua build.
-- Must match re1_rl.leg_replay.JOYPAD_BUTTON_ORDER / JOYPAD_TURBO_BIT.
local TAPE_ON = false
local TAPE_CHUNKS = {}
local TAPE_PENDING = {}
local TAPE_N = 0
local TAPE_PENDING_LIMIT = 1024
local TAPE_TURBO_BIT = 16384
local LAST_BTN = {}
local TAPE_BUTTON_ORDER = {
    "up", "down", "left", "right",
    "cross", "triangle", "square", "circle",
    "start", "select", "r1", "l1", "r2", "l2",
}

local function pack_buttons(btn)
    local bits = 0
    local p = 1
    btn = btn or {}
    for i = 1, #TAPE_BUTTON_ORDER do
        if btn[TAPE_BUTTON_ORDER[i]] == true then
            bits = bits + p
        end
        p = p * 2
    end
    return bits
end

local function tape_record(btn)
    if not TAPE_ON then
        return
    end
    local word = pack_buttons(btn) + (LAST_TURBO and TAPE_TURBO_BIT or 0)
    TAPE_PENDING[#TAPE_PENDING + 1] =
        B64:sub(math.floor(word / 4096) + 1, math.floor(word / 4096) + 1)
        .. B64:sub(math.floor(word / 64) % 64 + 1, math.floor(word / 64) % 64 + 1)
        .. B64:sub(word % 64 + 1, word % 64 + 1)
    TAPE_N = TAPE_N + 1
    if #TAPE_PENDING >= TAPE_PENDING_LIMIT then
        TAPE_CHUNKS[#TAPE_CHUNKS + 1] = table.concat(TAPE_PENDING)
        TAPE_PENDING = {}
    end
end

local function tape_packed_bytes()
    if #TAPE_PENDING > 0 then
        TAPE_CHUNKS[#TAPE_CHUNKS + 1] = table.concat(TAPE_PENDING)
        TAPE_PENDING = {}
    end
    return table.concat(TAPE_CHUNKS)
end

local function emu_advance()
    tape_record(LAST_BTN)
    emu.frameadvance()
end

local function apply_buttons(btn)
    LAST_BTN = btn or {}
    local out = {}
    for friendly, core_name in pairs(BUTTON_MAP) do
        out[core_name] = LAST_BTN[friendly] == true
    end
    joypad.set(out)
end

-- Latched across env steps: directions + run (square). Face buttons pulse per step.
local STICKY = { up = false, down = false, left = false, right = false, square = false }

local function sticky_frame_buttons(
    pulse, pulse_hold, frame_idx, pulse_on, pulse_off, pulse_from, pulse_through
)
    pulse_from = tonumber(pulse_from) or 1
    local btn = {}
    for k, v in pairs(STICKY) do
        if v then
            btn[k] = true
        end
    end
    if pulse_hold and next(pulse_hold) then
        for k, v in pairs(pulse_hold) do
            if v then
                btn[k] = true
            end
        end
    end
    if pulse and next(pulse) and frame_idx >= pulse_from then
        if pulse_through then
            for k, v in pairs(pulse) do
                if v then
                    btn[k] = true
                end
            end
        else
            pulse_on = pulse_on or 2
            pulse_off = pulse_off or 2
            local period = pulse_on + pulse_off
            if period > 0 and ((frame_idx - pulse_from) % period) < pulse_on then
                for k, v in pairs(pulse) do
                    if v then
                        btn[k] = true
                    end
                end
            end
        end
    end
    return btn
end

local function apply_sticky_hold()
    local btn = {}
    for k, v in pairs(STICKY) do
        if v then
            btn[k] = true
        end
    end
    apply_buttons(btn)
end

local function read_host_joypad(debug_axes)
    -- Pump SDL / main-window events while emulation is frozen on the socket.
    if emu.yield then
        emu.yield()
    end
    local j = joypad.getimmediate()
    if j == nil or next(j) == nil then
        j = joypad.getimmediate(1)
    end
    local out = {}
    for friendly, core_name in pairs(BUTTON_MAP) do
        local v = j[core_name]
        if v == true or v == 1 then
            out[friendly] = true
        end
    end
    -- Nymashock PSX: left stick is two 0..255 axes centered at 128 (see data/button_names.txt).
    local function stick128(v, neg_name, pos_name, dead)
        if type(v) ~= "number" then
            return
        end
        dead = dead or 24
        local d = v - 128
        if math.abs(d) < dead then
            return
        end
        if d < 0 then
            out[neg_name] = true
        else
            out[pos_name] = true
        end
    end
    stick128(j["P1 Left Stick Left / Right"], "left", "right")
    stick128(j["P1 Left Stick Up / Down"], "up", "down")
    -- Some BizHawk controller profiles expose stick as separate direction bits.
    local ALT_STICK = {
        up = { "P1 Up", "P1 Thumbstick Up", "P1 D-Pad Up" },
        down = { "P1 Down", "P1 Thumbstick Down", "P1 D-Pad Down" },
        left = { "P1 Left", "P1 Thumbstick Left", "P1 D-Pad Left" },
        right = { "P1 Right", "P1 Thumbstick Right", "P1 D-Pad Right" },
    }
    for friendly, names in pairs(ALT_STICK) do
        for _, name in ipairs(names) do
            local v = j[name]
            if v == true or v == 1 then
                out[friendly] = true
                break
            end
        end
    end
    -- Fallback for cores that expose generic signed axis names (not Nymashock 128-center).
    local x = j["P1 X Axis"] or j["P1 LStick X"]
    local y = j["P1 Y Axis"] or j["P1 LStick Y"]
    if type(x) == "number" or type(y) == "number" then
        x = tonumber(x) or 0
        y = tonumber(y) or 0
        if math.abs(x) <= 1.0 and math.abs(y) <= 1.0 then
            if x < -0.35 then out.left = true elseif x > 0.35 then out.right = true end
            if y < -0.35 then out.up = true elseif y > 0.35 then out.down = true end
        elseif math.abs(x) > 255 or math.abs(y) > 255 then
            if x < -16384 then out.left = true elseif x > 16384 then out.right = true end
            if y < -16384 then out.up = true elseif y > 16384 then out.down = true end
        end
    end
    local raw = nil
    if debug_axes then
        raw = {}
        for k, v in pairs(j) do
            raw[k] = v
        end
    end
    return out, raw
end

local function handle_command(cmd)
    local op = cmd.cmd

    if op == "ping" then
        return { ok = true, pong = cmd.n or 0 }

    elseif op == "read_ram" then
        local values = {}
        for _, field in ipairs(cmd.fields) do
            local name, addr, dtype = field[1], field[2], field[3]
            values[name] = read_field(addr, dtype)
        end
        return { ok = true, values = values }

    elseif op == "write_ram" then
        for _, field in ipairs(cmd.fields) do
            local _name, addr, dtype, value = field[1], field[2], field[3], field[4]
            write_field(addr, dtype, value)
        end
        return { ok = true }

    elseif op == "read_block" then
        local off = ps1_to_mainram(cmd.addr)
        local bytes = {}
        for i = 0, cmd.count - 1 do
            bytes[i + 1] = memory.readbyte(off + i, "MainRAM")
        end
        return { ok = true, addr = cmd.addr, bytes = bytes }

    elseif op == "list_domains" then
        local names = memory.getmemorydomainlist()
        local out = {}
        for i, name in ipairs(names) do
            local size = 0
            local ok, sz = pcall(memory.getmemorydomainsize, name)
            if ok and type(sz) == "number" then
                size = sz
            end
            out[i] = { name = name, size = size }
        end
        return { ok = true, domains = out }

    elseif op == "read_domain" then
        -- Raw domain read: cmd.domain, cmd.addr (domain offset), cmd.count
        local domain = cmd.domain or "MainRAM"
        local addr = tonumber(cmd.addr) or 0
        local count = tonumber(cmd.count) or 0
        local bytes = {}
        for i = 0, count - 1 do
            bytes[i + 1] = memory.readbyte(addr + i, domain)
        end
        return { ok = true, domain = domain, addr = addr, bytes = bytes }

    elseif op == "write_domain" then
        -- cmd.domain, cmd.addr, cmd.bytes = array of u8
        local domain = cmd.domain or "MainRAM"
        local addr = tonumber(cmd.addr) or 0
        local bytes = cmd.bytes or {}
        for i, b in ipairs(bytes) do
            memory.writebyte(addr + i - 1, tonumber(b) % 256, domain)
        end
        return { ok = true }

    elseif op == "buttons" then
        apply_buttons(cmd.buttons)
        return { ok = true }

    elseif op == "clear_input" then
        -- Episode boundary: drop latched sticky + any pending joypad.set so a
        -- fresh reset cannot advance one frame on the previous PPO hold.
        for k, _ in pairs(STICKY) do
            STICKY[k] = false
        end
        apply_buttons({})
        return { ok = true }

    elseif op == "read_joypad" then
        local out, raw = read_host_joypad(cmd.debug == true)
        -- dkjson encodes empty Lua tables as JSON arrays; keep a dummy key.
        out._ = false
        local resp = { ok = true, buttons = out }
        if raw then
            raw._ = false
            resp.raw = raw
        end
        return resp

    elseif op == "frameadvance" then
        local n = cmd.n or 1
        for _ = 1, n do
            apply_patches()
            emu_advance()
        end
        return { ok = true, frame = emu.framecount() }

    elseif op == "step" then
        -- joypad.set only lasts ONE frame; re-apply before every advance.
        -- sticky mode: directions + square latch; pulse buttons tap within the batch.
        -- legacy mode (cmd.buttons, no cmd.sticky): hold buttons for n frames then release.
        local n = cmd.n or 1
        local frame_buttons = cmd.frame_buttons
        local use_frame_buttons = type(frame_buttons) == "table" and #frame_buttons > 0
        if use_frame_buttons then
            n = #frame_buttons
        end
        local use_sticky = cmd.sticky ~= nil
        local legacy_btn = cmd.buttons or {}
        local pulse = cmd.pulse or {}
        local pulse_hold = cmd.pulse_hold or {}
        local pulse_on = tonumber(cmd.pulse_on) or 2
        local pulse_off = tonumber(cmd.pulse_off) or 2
        local pulse_from = tonumber(cmd.pulse_from) or 1
        local pulse_through = cmd.pulse_through == true
        -- frame_buttons macros (knife / standing gun) own the pad: never inherit
        -- latched walk / aim-down from a prior env step (R1+Down = floor aim).
        if use_frame_buttons then
            for k, _ in pairs(STICKY) do
                STICKY[k] = false
            end
        elseif use_sticky then
            for k, v in pairs(cmd.sticky) do
                if STICKY[k] ~= nil then
                    STICKY[k] = v == true
                end
            end
        end
        local hp_off = cmd.death_hp_addr and ps1_to_mainram(tonumber(cmd.death_hp_addr)) or nil
        local abort_on_zero_hp = cmd.abort_on_zero_hp == true
        local hp_floor = tonumber(cmd.hp_floor) or 0
        local hp_floor_chip_max = tonumber(cmd.hp_floor_chip_max) or 4
        local saw_positive_hp = false
        local death_during_step = false
        local last_hp = 0
        -- echo_joypad: read back joypad.get() after each advance so Python can
        -- verify BizHawk actually delivered the schedule (input-delivery QA).
        local echo = cmd.echo_joypad == true
        local joypad_echo = {}
        local capture_final_mmf = cmd.capture_final_mmf == true
        local mmf_name = cmd.mmf_name or ("re1_screenshot_" .. tostring(cmd.port or 0))
        if hp_off then
            last_hp = memory.read_u16_le(hp_off, "MainRAM")
            if last_hp > 0 then
                saw_positive_hp = true
            end
        end
        for i = 1, n do
            saw_positive_hp, death_during_step, last_hp = apply_hp_floor_check(
                hp_off, hp_floor, hp_floor_chip_max, abort_on_zero_hp,
                saw_positive_hp, last_hp
            )
            if death_during_step then
                break
            end
            if use_frame_buttons then
                apply_buttons(frame_buttons[i] or {})
            elseif use_sticky then
                apply_buttons(sticky_frame_buttons(
                    pulse, pulse_hold, i, pulse_on, pulse_off, pulse_from, pulse_through
                ))
            else
                apply_buttons(legacy_btn)
            end
            apply_patches()
            emu_advance()
            if echo then
                local j = joypad.get()
                local held = {}
                for friendly, core_name in pairs(BUTTON_MAP) do
                    if j[core_name] == true then
                        held[#held + 1] = friendly
                    end
                end
                table.sort(held)
                joypad_echo[i] = table.concat(held, "+")
            end
            saw_positive_hp, death_during_step, last_hp = apply_hp_floor_check(
                hp_off, hp_floor, hp_floor_chip_max, abort_on_zero_hp,
                saw_positive_hp, last_hp
            )
            if death_during_step then
                break
            end
        end
        if use_sticky or use_frame_buttons then
            apply_sticky_hold()
        else
            apply_buttons({})
        end
        local resp = {
            ok = true,
            frame = emu.framecount(),
            death_during_step = death_during_step,
        }
        if capture_final_mmf then
            local got, size, err = mmf_capture(mmf_name)
            if got and size and size > 0 then
                resp.final_mmf_name = got
                resp.final_mmf_size = size
                resp.final_mmf_frame = emu.framecount()
            elseif err then
                resp.final_mmf_error = err
            end
        end
        if echo then
            -- dkjson needs a hint to keep this an array when frames aborted early
            resp.joypad_echo = setmetatable(joypad_echo, { __jsontype = "array" })
        end
        return resp

    elseif op == "fast_forward" then
        -- Burn frames entirely Lua-side: one socket round-trip per chunk
        -- instead of one per mash tap. Three skip situations:
        --   cutscene/door/FMV: in-control bit CLEAR -> turbo + patches only
        --     (no button mash; engine patches advance doors/FMV fast enough)
        --   dialogue box:   in-control bit SET but message flag SET -> cross taps
        --   scripted scene: in-control bit SET but scene flag SET -> cross taps
        --     (never mash at 0 HP — Continue reload)
        local maxn = tonumber(cmd.max_frames) or 1200
        local mask = tonumber(cmd.mask) or 0x80
        local mode_off = ps1_to_mainram(tonumber(cmd.mode_addr))
        local msg_off = cmd.msg_addr and ps1_to_mainram(tonumber(cmd.msg_addr)) or nil
        local msg_mask = tonumber(cmd.msg_mask) or 0x80
        local scene_off = cmd.scene_addr and ps1_to_mainram(tonumber(cmd.scene_addr)) or nil
        local scene_mask = tonumber(cmd.scene_mask) or 0x10
        local turbo_speed = tonumber(cmd.speed) or 6400
        local restore_speed = tonumber(cmd.restore_speed) or 100
        local invisible = cmd.invisible == true
        -- scripted scenes flicker all-clear for a few frames between camera
        -- cuts; require the clear state to hold before handing control back
        local settle_need = tonumber(cmd.settle) or 10
        local hp_off = cmd.death_hp_addr and ps1_to_mainram(tonumber(cmd.death_hp_addr)) or nil
        local abort_on_zero_hp = cmd.abort_on_zero_hp == true
        local hp_floor = tonumber(cmd.hp_floor) or 0
        local hp_floor_chip_max = tonumber(cmd.hp_floor_chip_max) or 4
        local saw_positive_hp = false
        local death_abort = false
        local last_hp = 0

        local function bit_set(off, m)
            if not off then
                return false
            end
            local v = memory.readbyte(off, "MainRAM")
            return math.floor(v / m) % 2 == 1
        end
        -- Match re1_rl.ram_skip.scene_active_from_ram: bit 0x10 (hunter/dog)
        -- OR departure from idle 0x80 (Kenneth tea-room scare uses 0x84).
        local SCENE_FLAG_MASK = 0x10
        local function scene_active_byte(v)
            if math.floor(v / SCENE_FLAG_MASK) % 2 == 1 then
                return true
            end
            if math.floor(v % 128) ~= 0 then
                return true
            end
            return false
        end
        local function scene_active_read(off)
            if not off then
                return false
            end
            return scene_active_byte(memory.readbyte(off, "MainRAM"))
        end
        local function ctl()
            local m = memory.readbyte(mode_off, "MainRAM")
            return math.floor(m / mask) % 2 == 1, m
        end

        local burned = 0
        local in_control, mode = ctl()
        local msg = msg_off and bit_set(msg_off, msg_mask) or false
        local scene = scene_active_read(scene_off)
        -- Peak raw bytes for reward gating: Kenneth (0x84) often settles back to
        -- idle 0x80 at both Python endpoints; qualify needs mid-skip evidence.
        local peak_scene_flag = 0
        local peak_msg_flag = 0
        -- STAGE_ID 0x800C8660 / ROOM_ID 0x800C8661 — Wesker bounce 105→106→105.
        local stage_off = ps1_to_mainram(0x800C8660)
        local room_off = ps1_to_mainram(0x800C8661)
        local function room_code()
            local st = memory.readbyte(stage_off, "MainRAM")
            local rm = memory.readbyte(room_off, "MainRAM")
            return string.format("%d%02X", st + 1, rm)
        end
        local peak_room = room_code()
        if scene_off then
            peak_scene_flag = memory.readbyte(scene_off, "MainRAM")
        end
        if msg_off then
            peak_msg_flag = memory.readbyte(msg_off, "MainRAM")
        end
        local function note_peaks()
            if scene_off then
                local sf = memory.readbyte(scene_off, "MainRAM")
                if scene_active_byte(sf) then
                    peak_scene_flag = sf
                elseif peak_scene_flag == 0 then
                    peak_scene_flag = sf
                end
            end
            if msg_off then
                local mf = memory.readbyte(msg_off, "MainRAM")
                if bit_set(msg_off, msg_mask) then
                    peak_msg_flag = mf
                elseif peak_msg_flag == 0 then
                    peak_msg_flag = mf
                end
            end
            local rc = room_code()
            if rc == "106" then
                peak_room = rc
            end
        end
        note_peaks()
        if hp_off then
            last_hp = memory.read_u16_le(hp_off, "MainRAM")
            if last_hp > 0 then
                saw_positive_hp = true
            end
        end
        if (not in_control) or msg or scene then
            client.speedmode(turbo_speed)
            if invisible then
                set_invisible(true)
            end
            local settle = 0
            while burned < maxn do
                saw_positive_hp, death_abort, last_hp = apply_hp_floor_check(
                    hp_off, hp_floor, hp_floor_chip_max, abort_on_zero_hp,
                    saw_positive_hp, last_hp
                )
                if death_abort then
                    -- Hunter/dog death uses scene_flag while in-control;
                    -- abort before cross-mash reloads from Continue.
                    break
                end
                local btn = {}
                local hp_zero = false
                if hp_off then
                    hp_zero = memory.read_u16_le(hp_off, "MainRAM") <= 0
                end
                if in_control and (msg or scene) and burned % 12 < 4 and not hp_zero then
                    -- modal dialogue / scripted scene: tap cross with a wide
                    -- release window so each text box gets a fresh press edge.
                    btn = { cross = true }
                elseif not in_control and burned % 12 < 4 and not hp_zero then
                    -- engine-controlled pickup/door spans still need confirm.
                    btn = { cross = true }
                end
                apply_buttons(btn)
                apply_patches(true)
                emu_advance()
                burned = burned + 1
                saw_positive_hp, death_abort, last_hp = apply_hp_floor_check(
                    hp_off, hp_floor, hp_floor_chip_max, abort_on_zero_hp,
                    saw_positive_hp, last_hp
                )
                if death_abort then
                    break
                end
                in_control, mode = ctl()
                msg = msg_off and bit_set(msg_off, msg_mask) or false
                scene = scene_active_read(scene_off)
                note_peaks()
                if in_control and not msg and not scene then
                    settle = settle + 1
                    if settle >= settle_need then
                        break
                    end
                else
                    settle = 0
                end
            end
            apply_buttons({})
            -- restore the turbo halfword if control returned (unforced pass)
            apply_patches(false)
            if invisible then
                set_invisible(false)
            end
            client.speedmode(restore_speed)
        end
        return {
            ok = true,
            burned = burned,
            mode = mode,
            in_control = in_control,
            msg_open = msg,
            scene_active = scene,
            peak_scene_flag = peak_scene_flag,
            peak_msg_flag = peak_msg_flag,
            peak_room = peak_room,
            death_abort = death_abort,
            frame = emu.framecount(),
        }

    elseif op == "set_patches" then
        PATCHES.always = cmd.always or {}
        PATCHES.turbo = cmd.turbo
        apply_patches()
        return { ok = true, n = #PATCHES.always }

    elseif op == "loadstate" then
        savestate.load(cmd.path)
        -- savestates revert MainRAM to pre-patch bytes; re-apply immediately
        apply_patches()
        return { ok = true }

    elseif op == "savestate" then
        savestate.save(cmd.path)
        return { ok = true }

    elseif op == "screenshot" then
        local path = cmd.path or SHOT_PATH
        client.screenshot(path)
        -- client.screenshot always pops "{filename} saved" on the OSD; flood it
        -- off immediately so training doesn't paint the screen every step.
        for _ = 1, 32 do
            gui.addmessage("")
        end
        return { ok = true, path = path }

    elseif op == "screenshot_b64" then
        -- Benchmark: MMF capture, Lua reads PNG bytes, base64 in JSON (no _frame_*.png).
        local mmf_name = cmd.mmf_name or ("re1_screenshot_" .. tostring(cmd.port or 0))
        local got, size, err = mmf_capture(mmf_name)
        if err then
            return { ok = false, error = err }
        end
        local png_bytes = ""
        if comm.mmfReadBytes then
            local bytes = comm.mmfReadBytes(got, size)
            if type(bytes) == "table" then
                local parts = {}
                for i = 0, size - 1 do
                    local b = bytes[i]
                    if b == nil then
                        break
                    end
                    parts[#parts + 1] = string.char(b)
                end
                png_bytes = table.concat(parts)
            end
        elseif comm.mmfRead then
            png_bytes = comm.mmfRead(got, size) or ""
        end
        if png_bytes == nil or #png_bytes == 0 then
            return { ok = false, error = "mmf read returned empty PNG" }
        end
        return { ok = true, png_b64 = base64_encode(png_bytes), size = size }

    elseif op == "screenshot_mmf" then
        -- Benchmark: MMF capture; Python reads tag via mmap (no _frame_*.png).
        local mmf_name = cmd.mmf_name or ("re1_screenshot_" .. tostring(cmd.port or 0))
        local got, size, err = mmf_capture(mmf_name)
        if err then
            return { ok = false, error = err }
        end
        return { ok = true, mmf_name = got, size = size }

    elseif op == "speed" then
        client.speedmode(cmd.percent or 100)
        return { ok = true }

    elseif op == "invisible" then
        -- skip rendering entirely (TAS-bot mode); used while fast-forwarding
        -- door animations / cutscenes so they are neither seen nor throttled
        set_invisible(cmd.on == true)
        return { ok = true }

    elseif op == "framecount" then
        return { ok = true, frame = emu.framecount() }

    elseif op == "reboot" then
        client.reboot_core()
        apply_patches()
        return { ok = true, frame = emu.framecount() }

    elseif op == "quit" then
        return { ok = true, bye = true }

    elseif op == "tape_enable" then
        TAPE_ON = cmd.on == true
        return { ok = true, on = TAPE_ON, n = TAPE_N }

    elseif op == "tape_clear" then
        TAPE_CHUNKS = {}
        TAPE_PENDING = {}
        TAPE_N = 0
        return { ok = true, n = 0 }

    elseif op == "tape_dump" then
        return {
            ok = true,
            n = TAPE_N,
            encoding = "b64x3_buttons14_turbo14",
            packed = tape_packed_bytes(),
        }

    elseif op == "tape_play" then
        local frames = cmd.frames or {}
        local n = #frames
        -- Policy holds must match env.step (apply_patches()): turbo only when
        -- not in_control. Scene-flag turbo here shortens in-control grab/hitstun
        -- that recording billed as skip=0. Skip spans use force turbo, matching
        -- fast_forward's apply_patches(true).
        local patch_mode = cmd.patch_mode
        if patch_mode == nil or patch_mode == "" then
            if cmd.no_cutscene_turbo == true then
                patch_mode = "step"
            else
                patch_mode = "skip"
            end
        end
        for i = 1, n do
            local bits = tonumber(frames[i]) or 0
            local btn = {}
            local p = 1
            for b = 1, #TAPE_BUTTON_ORDER do
                if math.floor(bits / p) % 2 == 1 then
                    btn[TAPE_BUTTON_ORDER[b]] = true
                end
                p = p * 2
            end
            apply_buttons(btn)
            if patch_mode == "off" then
                apply_patches("off")
            elseif patch_mode == "step" then
                apply_patches()
            elseif patch_mode == "force" then
                apply_patches(true)
            else
                apply_patches(tape_skip_force_turbo())
            end
            emu_advance()
        end
        return { ok = true, n = n, frame = emu.framecount() }

    else
        return { ok = false, error = "unknown cmd: " .. tostring(op) }
    end
end

if not comm.socketServerIsConnected() then
    error("re1_client: comm socket not connected. Launch EmuHawk with "
        .. "--socket_ip=127.0.0.1 --socket_port=5555 (server must be running first).")
end
comm.socketServerSetTimeout(600000)  -- 10 min; Python drives the pace
console.log("re1_client: comm socket " .. comm.socketServerGetInfo())

comm.socketServerSend(json.encode({ hello = "re1_client", frame = emu.framecount() }))
-- Command-line A/V dumping begins before Lua. Handshake first so an optional
-- pause failure can never make Python wait forever for the hello.
pcall(client.pause_av)

while true do
    local payload = comm.socketServerResponse()
    if payload == nil or payload == "" then
        console.log("re1_client: empty response (timeout/disconnect), exiting")
        break
    end

    local cmd, _, decode_err = json.decode(payload)
    local resp
    if not cmd then
        resp = { ok = false, error = "bad json: " .. tostring(decode_err) }
    else
        local rok, r = pcall(handle_command, cmd)
        resp = rok and r or { ok = false, error = tostring(r) }
    end

    comm.socketServerSend(json.encode(resp))

    if cmd and cmd.cmd == "quit" then break end
end

console.log("re1_client: done")
