# Vendored from https://github.com/AndBondStyle/niimprint (fork of
# https://github.com/kjy00302/niimprint), Copyright (c) 2023 kjy00302, MIT, see LICENSE
# at commit 35c41a705cbc25ded388f1d562f184bf8994080e, with small fixes:
# - relative imports, transport close(), clear error when RFCOMM is unavailable
# - _recv() no longer spins forever on partial packets and resyncs on garbage
# - requests raise TimeoutError instead of AttributeError when the printer is silent
# - print_image() gives up waiting for end_print after 30 s
# - serial auto-detect ignores non-USB ports (macOS always has two)
# - get_rfid() tolerates trailing bytes and serials decode as ASCII (B1)
# - print_image_v2(): newer print flow needed by the B1, based on the protocol
#   research in niimbluelib by MultiMote (https://github.com/MultiMote/niimblue)
from .packet import NiimbotPacket
from .printer import (
    BaseTransport,
    BluetoothTransport,
    InfoEnum,
    PrinterClient,
    RequestCodeEnum,
    SerialTransport,
)
