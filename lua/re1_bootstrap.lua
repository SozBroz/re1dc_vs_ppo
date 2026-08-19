-- Tiny BizHawk command-line entrypoint. Keep this equivalent to the WH2 probe
-- that executes the client successfully; in particular, do not tail-return it.
local chunk, err =
  loadfile("C:/Users/sshuser/re1_rl/lua/re1_client.lua")
if not chunk then
  chunk, err = loadfile("D:/re1_rl/lua/re1_client.lua")
end
if not chunk then
  error("re1_bootstrap: cannot load re1_client.lua: " .. tostring(err))
end
chunk()
