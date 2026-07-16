@echo off
REM Canonical RE1 fleet LAN addresses (set static via tools\set_fleet_static_ip.ps1).
REM Retired DHCP drift: workhorse1 was .160, workhorse2 was .111.
set FLEET_WH1_HOST=192.168.0.203
set FLEET_WH2_HOST=192.168.0.116
set FLEET_LEARNER_HOST=192.168.0.116
set FLEET_LEARNER_PORT=8765
