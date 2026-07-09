"""Poll pygame joystick while EmuHawk holds the same device."""
import os
import sys
import time

os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
os.environ["SDL_GAMECONTROLLER_ALLOW_BACKGROUND_EVENTS"] = "1"

import pygame

pygame.init()
pygame.joystick.init()
print(f"joysticks: {pygame.joystick.get_count()}", flush=True)
if pygame.joystick.get_count() == 0:
    sys.exit("no pad")
j = pygame.joystick.Joystick(0)
j.init()
print(f"name: {j.get_name()}", flush=True)
for i in range(50):
    pygame.event.pump()
    ax = [round(j.get_axis(k), 3) for k in range(min(4, j.get_numaxes()))]
    bt = [j.get_button(k) for k in range(min(14, j.get_numbuttons())) if j.get_button(k)]
    print(f"{i}: axes={ax} buttons={bt}", flush=True)
    time.sleep(0.1)
