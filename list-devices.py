#!/usr/bin/env python3
# coding=utf-8


import evdev


something_found = False
print("Available input devices:")
for d in evdev.list_devices():
    print(evdev.InputDevice(d).name)
    something_found = True


if not something_found:
    print("No input devices found. Please connect a device and try again. Ensure you have the necessary permissions to access input devices (e.g., by adding the current user to the \"input\" group).")
    exit(1)

