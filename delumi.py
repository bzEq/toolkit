#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Switch some ports of `RTS5411 Hub' off. WIP.
# ID 0bda:5411 Realtek Semiconductor Corp. RTS5411 Hub
# Hub Descriptor:
#   bLength               9
#   bDescriptorType      41
#   nNbrPorts             4
#   wHubCharacteristic 0x00a9
#     Per-port power switching
#     Per-port overcurrent protection
#     TT think time 16 FS bits
#     Port indicators
#   bPwrOn2PwrGood        0 * 2 milli seconds
#   bHubContrCurrent    100 milli Ampere
#   DeviceRemovable    0x00
#   PortPwrCtrlMask    0xff

import os
import sys
import argparse
import usb

USB_PORT_FEAT_POWER = 8


def main():
    b = list(usb.busses())
    my_hub = b[0].devices[2]
    h = my_hub.open()
    try:
        h.controlMsg(requestType=usb.TYPE_CLASS | usb.RECIP_OTHER,
                     request=usb.REQ_CLEAR_FEATURE,
                     value=USB_PORT_FEAT_POWER,
                     index=1,
                     buffer=None,
                     timeout=1000)
    finally:
        h.finalize()


if __name__ == '__main__':
    sys.exit(main())
