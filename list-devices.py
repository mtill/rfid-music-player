#!/usr/bin/env python3
# coding=utf-8


import evdev


for d in evdev.list_devices():
    print(evdev.InputDevice(d).name)

