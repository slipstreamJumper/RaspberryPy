from __future__ import print_function

import sys, time
from raspberrypy.control.myo import Myo

import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BOARD)
GPIO.setup(7, GPIO.OUT)
GPIO.setup(11, GPIO.OUT)
upper = GPIO.PWM(7, 50)
lower = GPIO.PWM(11, 50)
upper.start(7.5)
lower.start(7.5)

global_current_pose = "REST"

if __name__ == '__main__':
  m = Myo(sys.argv[1] if len(sys.argv) >= 2 else None)

  def proc_emg(emg, moving, times=[]):
    print(emg)

    ## print framerate of received data
    times.append(time.time())
    if len(times) > 20:
      #print((len(times) - 1) / (times[-1] - times[0]))
      times.pop(0)

  def set_current_pose(pose):
    global_current_pose = pose


  def move_arm():
    upper.ChangeDutyCycle(m.gyro_z)  # turn towards 90 degree
    lower.ChangeDutyCycle(m.gyro_x)  # turn towards 90 degree
    '''
    if pose == "REST":
      upper.ChangeDutyCycle(7.5)  # turn towards 90 degree
      lower.ChangeDutyCycle(7.5)  # turn towards 90 degree

    elif pose == "FIST":
      upper.ChangeDutyCycle(2.5)  # turn towards 0 degree
      upper.ChangeDutyCycle(12.5)  # turn towards 180 degree
      lower.ChangeDutyCycle(2.5)  # turn towards 0 degree
      lower.ChangeDutyCycle(12.5)  # turn towards 180 degree
    '''
# m.add_emg_handler(proc_emg)
  m.connect()


  current_pose = ""
  m.add_arm_handler(lambda arm, xdir: print('arm', arm, 'xdir', xdir))
  m.add_pose_handler(lambda p: move_arm())
  #print('pose', global_current_pose)


  try:
    while True:
      m.run(1)

  except KeyboardInterrupt:
    pass
  finally:
    print("max x: " + str(m.max_x) + " min x: " + str(m.min_x))
    print("max y: " + str(m.max_y) + " min y: " + str(m.min_y))
    print("max z: " + str(m.max_z) + " min z: " + str(m.min_z))
    m.disconnect()
    print()
