# This script is modified from 
#   https://github.com/dzhu/myo-raw 
#   http://www.fernandocosentino.net/pyoconnect/
#

# to enable Myo please execute
# // permission to ttyACM0 - must restart linux user after this
# sudo usermod -a -G dialout $USER
# // dependencies
# sudo pip install pySerial enum34

from __future__ import print_function

import enum
import re
import struct
import sys
import threading
import time

import serial
from serial.tools.list_ports import comports

# common.py
import struct

def pack(fmt, *args):
  return struct.pack('<' + fmt, *args)

def unpack(fmt, *args):
  return struct.unpack('<' + fmt, *args)

def text(scr, font, txt, pos, clr=(255,255,255)):
  scr.blit(font.render(txt, True, clr), pos)
# commmon.py end


def multichr(ords):
  if sys.version_info[0] >= 3:
    return bytes(ords)
  else:
    return ''.join(map(chr, ords))

def multiord(b):
  if sys.version_info[0] >= 3:
    return list(b)
  else:
    return map(ord, b)

class Arm(enum.Enum):
  UNKNOWN = 0
  RIGHT = 1
  LEFT = 2

class XDirection(enum.Enum):
  UNKNOWN = 0
  X_TOWARD_WRIST = 1
  X_TOWARD_ELBOW = 2

class Pose(enum.Enum):
  REST = 0
  FIST = 1
  WAVE_IN = 2
  WAVE_OUT = 3
  FINGERS_SPREAD = 4
  THUMB_TO_PINKY = 5
  UNKNOWN = 255

class Packet(object):
  def __init__(self, ords):
    self.typ = ords[0]
    self.cls = ords[2]
    self.cmd = ords[3]
    self.payload = multichr(ords[4:])

  def __repr__(self):
    return 'Packet(%02X, %02X, %02X, [%s])' % \
      (self.typ, self.cls, self.cmd,
       ' '.join('%02X' % b for b in multiord(self.payload)))


class BT(object):
  '''Implements the non-Myo-specific details of the Bluetooth protocol.'''
  def __init__(self, tty):
    self.ser = serial.Serial(port=tty, baudrate=9600, dsrdtr=1)
    self.buf = []
    self.lock = threading.Lock()
    self.handlers = []

  ## internal data-handling methods
  def recv_packet(self, timeout=None):
    t0 = time.time()
    self.ser.timeout = None
    while timeout is None or time.time() < t0 + timeout:
      if timeout is not None: self.ser.timeout = t0 + timeout - time.time()
      c = self.ser.read()
      if not c: return None

      ret = self.proc_byte(ord(c))
      if ret:
        if ret.typ == 0x80:
          self.handle_event(ret)
        return ret

  def recv_packets(self, timeout=.5):
    res = []
    t0 = time.time()
    while time.time() < t0 + timeout:
      p = self.recv_packet(t0 + timeout - time.time())
      if not p: return res
      res.append(p)
    return res

  def proc_byte(self, c):
    if not self.buf:
      if c in [0x00, 0x80, 0x08, 0x88]:
        self.buf.append(c)
      return None
    elif len(self.buf) == 1:
      self.buf.append(c)
      self.packet_len = 4 + (self.buf[0] & 0x07) + self.buf[1]
      return None
    else:
      self.buf.append(c)

    if self.packet_len and len(self.buf) == self.packet_len:
      p = Packet(self.buf)
      self.buf = []
      return p
    return None

  def handle_event(self, p):
    for h in self.handlers:
      h(p)

  def add_handler(self, h):
    self.handlers.append(h)

  def remove_handler(self, h):
    try: self.handlers.remove(h)
    except ValueError: pass

  def wait_event(self, cls, cmd):
    res = [None]
    def h(p):
      if p.cls == cls and p.cmd == cmd:
        res[0] = p
    self.add_handler(h)
    while res[0] is None:
      self.recv_packet()
    self.remove_handler(h)
    return res[0]

  ## specific BLE commands
  def connect(self, addr):
    return self.send_command(6, 3, pack('6sBHHHH', multichr(addr), 0, 6, 6, 64, 0))

  def get_connections(self):
    return self.send_command(0, 6)

  def discover(self):
    return self.send_command(6, 2, b'\x01')

  def end_scan(self):
    return self.send_command(6, 4)

  def disconnect(self, h):
    return self.send_command(3, 0, pack('B', h))

  def read_attr(self, con, attr):
    self.send_command(4, 4, pack('BH', con, attr))
    return self.wait_event(4, 5)

  def write_attr(self, con, attr, val):
    self.send_command(4, 5, pack('BHB', con, attr, len(val)) + val)
    return self.wait_event(4, 1)

  def send_command(self, cls, cmd, payload=b'', wait_resp=True):
    s = pack('4B', 0, len(payload), cls, cmd) + payload
    self.ser.write(s)

    while True:
      p = self.recv_packet()

      ## no timeout, so p won't be None
      if p.typ == 0: return p

      ## not a response: must be an event
      self.handle_event(p)


class Myo(object):
  '''Implements the Myo-specific communication protocol.'''

  def __init__(self, tty=None):
    if tty is None:
      tty = self.detect_tty()
    if tty is None:
      raise ValueError('Myo dongle not found!')

    self.bt = BT(tty)
    self.conn = None
    self.emg_handlers = []
    self.imu_handlers = []
    self.arm_handlers = []
    self.pose_handlers = []

    self.gyro_x = 0
    self.gyro_y = 0
    self.gyro_z = 0

    self.max_x = 1
    self.min_x = 0
    self.max_y = 1
    self.min_y = 0
    self.max_z = 1
    self.min_z = 0

  def detect_tty(self):
    for p in comports():
      if re.search(r'PID=2458:0*1', p[2]):
        print('using device:', p[0])
        return p[0]
    return None

  def run(self, timeout=None):
    self.bt.recv_packet(timeout)

  def normalize_val(self, x, min, max):
    return (x - min) // (max - min)

  def connect(self):
    ## stop everything from before
    self.bt.end_scan()
    self.bt.disconnect(0)
    self.bt.disconnect(1)
    self.bt.disconnect(2)

    ## start scanning
    print('scanning...')
    self.bt.discover()
    while True:
      p = self.bt.recv_packet()
      print('scan response:', p)

      if p.payload.endswith(b'\x06\x42\x48\x12\x4A\x7F\x2C\x48\x47\xB9\xDE\x04\xA9\x01\x00\x06\xD5'):
        addr = list(multiord(p.payload[2:8]))
        break
    self.bt.end_scan()

    ## connect and wait for status event
    conn_pkt = self.bt.connect(addr)
    self.conn = multiord(conn_pkt.payload)[-1]
    self.bt.wait_event(3, 0)

    ## get firmware version
    fw = self.read_attr(0x17)
    _, _, _, _, v0, v1, v2, v3 = unpack('BHBBHHHH', fw.payload)
    print('firmware version: %d.%d.%d.%d' % (v0, v1, v2, v3))

    self.old = (v0 == 0)

    if self.old:
      ## don't know what these do; Myo Connect sends them, though we get data
      ## fine without them
      self.write_attr(0x19, b'\x01\x02\x00\x00')
      self.write_attr(0x2f, b'\x01\x00')
      self.write_attr(0x2c, b'\x01\x00')
      self.write_attr(0x32, b'\x01\x00')
      self.write_attr(0x35, b'\x01\x00')

      ## enable EMG data
      self.write_attr(0x28, b'\x01\x00')
      ## enable IMU data
      self.write_attr(0x1d, b'\x01\x00')

      ## Sampling rate of the underlying EMG sensor, capped to 1000. If it's
      ## less than 1000, emg_hz is correct. If it is greater, the actual
      ## framerate starts dropping inversely. Also, if this is much less than
      ## 1000, EMG data becomes slower to respond to changes. In conclusion,
      ## 1000 is probably a good value.
      C = 1000
      emg_hz = 50
      ## strength of low-pass filtering of EMG data
      emg_smooth = 100

      imu_hz = 50

      ## send sensor parameters, or we don't get any data
      self.write_attr(0x19, pack('BBBBHBBBBB', 2, 9, 2, 1, C, emg_smooth, C // emg_hz, imu_hz, 0, 0))

    else:
      name = self.read_attr(0x03)
      print('device name: %s' % name.payload)

      ## enable IMU data
      self.write_attr(0x1d, b'\x01\x00')
      ## enable on/off arm notifications
      self.write_attr(0x24, b'\x02\x00')

      # self.write_attr(0x19, b'\x01\x03\x00\x01\x01')
      self.start_raw()

    ## add data handlers
    def handle_data(p):
      if (p.cls, p.cmd) != (4, 5): return

      c, attr, typ = unpack('BHB', p.payload[:4])
      pay = p.payload[5:]

      if attr == 0x27:
        vals = unpack('8HB', pay)
        ## not entirely sure what the last byte is, but it's a bitmask that
        ## seems to indicate which sensors think they're being moved around or
        ## something
        emg = vals[:8]
        moving = vals[8]
        self.on_emg(emg, moving)
      elif attr == 0x1c:
        vals = unpack('10h', pay)
        quat = vals[:4]
        acc = vals[4:7]
        gyro = vals[7:10]
        self.on_imu(quat, acc, gyro)
        #print(quat, acc, gyro)

        self.gyro_x = acc[0]//100.
        self.gyro_y = acc[1]//100.
        self.gyro_z = acc[2]//100.



        if self.gyro_x > self.max_x: self.max_x = self.gyro_x
        if self.gyro_x < self.min_x: self.min_x = self.gyro_x

        if self.gyro_y > self.max_y: self.max_y = self.gyro_y
        if self.gyro_y < self.min_y: self.min_y = self.gyro_y

        if self.gyro_z > self.max_z: self.max_z = self.gyro_z
        if self.gyro_z < self.min_z: self.min_z = self.gyro_z

        #self.gyro_x = self.normalize_val(self.gyro_x, self.min_x, self.max_x)
        #self.gyro_y = self.normalize_val(self.gyro_y, self.min_y, self.max_y)
        #self.gyro_z = self.normalize_val(self.gyro_z, self.min_z, self.max_z)


        print("X: " + str(self.gyro_x) + " Y: " + str(self.gyro_y) + " Z: " + str(self.gyro_z))

      elif attr == 0x23:
        typ, val, xdir, _, _ , _ = unpack('6B', pay)

        if typ == 1: # on arm
          self.on_arm(Arm(val), XDirection(xdir))
        elif typ == 2: # removed from arm
          self.on_arm(Arm.UNKNOWN, XDirection.UNKNOWN)
        elif typ == 3: # pose
          self.on_pose(Pose(val))
      else:
        print('data with unknown attr: %02X %s' % (attr, p))

    self.bt.add_handler(handle_data)


  def write_attr(self, attr, val):
    if self.conn is not None:
      self.bt.write_attr(self.conn, attr, val)

  def read_attr(self, attr):
    if self.conn is not None:
      return self.bt.read_attr(self.conn, attr)
    return None

  def disconnect(self):
    if self.conn is not None:
      self.bt.disconnect(self.conn)

  def start_raw(self):
    '''Sending this sequence for v1.0 firmware seems to enable both raw data and
    pose notifications.
    '''

    self.write_attr(0x28, b'\x01\x00')
    #self.write_attr(0x19, b'\x01\x03\x01\x01\x00')
    self.write_attr(0x19, b'\x01\x03\x01\x01\x01')

  def mc_start_collection(self):
    '''Myo Connect sends this sequence (or a reordering) when starting data
    collection for v1.0 firmware; this enables raw data but disables arm and
    pose notifications.
    '''

    self.write_attr(0x28, b'\x01\x00')
    self.write_attr(0x1d, b'\x01\x00')
    self.write_attr(0x24, b'\x02\x00')
    self.write_attr(0x19, b'\x01\x03\x01\x01\x01')
    self.write_attr(0x28, b'\x01\x00')
    self.write_attr(0x1d, b'\x01\x00')
    self.write_attr(0x19, b'\x09\x01\x01\x00\x00')
    self.write_attr(0x1d, b'\x01\x00')
    self.write_attr(0x19, b'\x01\x03\x00\x01\x00')
    self.write_attr(0x28, b'\x01\x00')
    self.write_attr(0x1d, b'\x01\x00')
    self.write_attr(0x19, b'\x01\x03\x01\x01\x00')

  def mc_end_collection(self):
    '''Myo Connect sends this sequence (or a reordering) when ending data collection
    for v1.0 firmware; this reenables arm and pose notifications, but
    doesn't disable raw data.
    '''

    self.write_attr(0x28, b'\x01\x00')
    self.write_attr(0x1d, b'\x01\x00')
    self.write_attr(0x24, b'\x02\x00')
    self.write_attr(0x19, b'\x01\x03\x01\x01\x01')
    self.write_attr(0x19, b'\x09\x01\x00\x00\x00')
    self.write_attr(0x1d, b'\x01\x00')
    self.write_attr(0x24, b'\x02\x00')
    self.write_attr(0x19, b'\x01\x03\x00\x01\x01')
    self.write_attr(0x28, b'\x01\x00')
    self.write_attr(0x1d, b'\x01\x00')
    self.write_attr(0x24, b'\x02\x00')
    self.write_attr(0x19, b'\x01\x03\x01\x01\x01')

  def vibrate(self, length):
    if length in xrange(1, 4):
      ## first byte tells it to vibrate; purpose of second byte is unknown
      self.write_attr(0x19, pack('3B', 3, 1, length))


  def add_emg_handler(self, h):
    self.emg_handlers.append(h)

  def add_imu_handler(self, h):
    self.imu_handlers.append(h)

  def add_pose_handler(self, h):
    self.pose_handlers.append(h)

  def add_arm_handler(self, h):
    self.arm_handlers.append(h)


  def on_emg(self, emg, moving):
    for h in self.emg_handlers:
      h(emg, moving)

  def on_imu(self, quat, acc, gyro):
    for h in self.imu_handlers:
      h(quat, acc, gyro)

  def on_pose(self, p):
    for h in self.pose_handlers:
      h(p)

  def on_arm(self, arm, xdir):
    for h in self.arm_handlers:
      h(arm, xdir)
