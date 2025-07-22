#!/usr/bin/env python3
# coding=utf-8


import time
from evdev import InputDevice, ecodes, list_devices
from select import select


class RFIDReader:

    def _doInit(self):
        devices = [InputDevice(fn) for fn in list_devices()]
        for device in devices:
            if device.name == self.rfidReaderName:
                self.dev = device
                #print(f"Using RFID reader: {self.rfidReaderName}")
                break

    def __init__(self, rfidReaderName):
        self.rfidReaderName = rfidReaderName

        self.keys = "X^1234567890XXXXqwertzuiopXXXXasdfghjklXXXXXyxcvbnmXXXXXXXXXXXXXXXXXXXXXXX"
        self.dev = None
        self._doInit()

    def _readCard(self):
        stri = ''
        key = ''
        while key != 'KEY_ENTER':
            r, w, x = select([self.dev], [], [])
            for event in self.dev.read():
                if event.type == 1 and event.value == 1:
                    stri += self.keys[event.code]
                    key = ecodes.KEY[event.code]
        return stri[:-1]

    def readCard(self):
        while True:
            try:
                return self._readCard()
            except:
                time.sleep(3)
                self._doInit()
