-- Tiny BizHawk command-line entrypoint. WH2 can stall when the full client is
-- supplied directly to --lua, but executes it normally through loadfile().
local candidates = {
  "C:/Users/sshuser/re1_rl/lua/re1_client.lua",
  "D:/re1_rl/lua/re1_client.lua",
  "./lua/re1_client.lua",
}

local errors = {}
for _, path in ipairs(candidates) do
  local chunk, err = loadfile(path)
  if chunk then
    return chunk()
  end
  errors[#errors + 1] = path .. ": " .. tostring(err)
end

error("re1_bootstrap: cannot load re1_client.lua\n" .. table.concat(errors, "\n"))
