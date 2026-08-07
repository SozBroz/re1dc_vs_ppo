@echo off
cd /d D:\re1_rl
echo Gallery cp33 RAM monitor harness
echo   CHECK 1: puzzle complete -^> episode continues
echo   CHECK 2: star crest pickup -^> checkpoint_success end
echo   Log: data\logs\gallery_cp33_harness.jsonl
venv\Scripts\python.exe scripts\play_gallery_cp33_harness.py --input both --speed 200
