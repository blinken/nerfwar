#!/usr/bin/env python
# -*- coding: utf-8 -*-

# todo:
#  * test GPIO control methods individually
#  * calibrate get_firing_angle
#  * do we want to perhaps only return to the rest position if no faces detected after some delay?

import pigpio
import picamera

import random
import time
import boto3
import json
from threading import Thread, Condition, Event
import paho.mqtt.client as mqtt

# GPIO mappings
GPIO_WARMUP = 18 # active low
GPIO_TRIGGER = 17 # active low
GPIO_TURNTABLE = 14 # servo

# 0 and 180 values for the servo
SERVO_MIN = 500
SERVO_MAX = 2250

# How long should we warmup and fire for? (seconds)
WARMUP_DELAY = 0.7
FIRE_TIME = 0.5
AIM_DELAY = 0.5 # how long to turn to position (TTFN is max(aim_delay, warmup_delay))

DEGREE = u'°'

gpio = pigpio.pi()
gpio.set_mode(GPIO_WARMUP, pigpio.OUTPUT)
gpio.write(GPIO_WARMUP, 1)

gpio.set_mode(GPIO_TRIGGER, pigpio.OUTPUT)
gpio.write(GPIO_TRIGGER, 1)

gpio.set_mode(GPIO_TURNTABLE, pigpio.OUTPUT)
gpio.set_servo_pulsewidth(GPIO_TURNTABLE, SERVO_MIN)

flag_fire = Event()
flag_shutdown = Event()

global_angle = 90
global_person = False

camera = picamera.PiCamera(resolution="VGA")
camera.rotation = 180
camera.hflip = True

AWS_REGION = "eu-west-1"
S3_BUCKET = 'blinken-devel'
S3_KEY = "rekognition.jpg"
s3_client = boto3.resource('s3', AWS_REGION)
r_client = boto3.client('rekognition', AWS_REGION)

def init():
  print "init: done\n",

def fire():
  while True:
    flag_fire.wait()

    print "fire: warming up\n",
    gpio.write(GPIO_WARMUP, 0)
    time.sleep(WARMUP_DELAY)

    print "fire: firing\n",
    gpio.write(GPIO_TRIGGER, 0)
    time.sleep(FIRE_TIME)

    print "fire: shutting down\n",
    gpio.write(GPIO_TRIGGER, 1)
    gpio.write(GPIO_WARMUP, 1)

    print "fire: done, sleeping 2\n",
    time.sleep(2)

# angle from 0..180
def aim(angle, reason):
  if angle < 0 or angle > 180:
    raise Exception("Angle must be between 0%s and 180%s, got %d" % (DEGREE, DEGREE, angle))

  print "aim: moving to %.2f%s (%s)\n" % (angle, DEGREE, reason),
  duty = (float(angle) / 180.0) * (SERVO_MAX - SERVO_MIN) + SERVO_MIN
  gpio.set_servo_pulsewidth(GPIO_TURNTABLE, duty)
  time.sleep(AIM_DELAY)

  print "aim: done (%s)\n" % (reason),

# move to the rest position
def rest():
  print "rest: moving to rest position\n",
  if gpio.get_servo_pulsewidth(GPIO_TURNTABLE) != 0:
      aim(0, "rest")
      gpio.set_servo_pulsewidth(GPIO_TURNTABLE, 0)

def get_image():
  while True:
    if flag_shutdown.is_set():
      print "camera: shutting down"
      break

    filename = "/tmp/nerf-input.jpg"
    print "camera: removing any existing %s" % filename
    try:
      os.remove(filename)
    except:
      pass

    print "camera: taking photo"
    camera.capture(filename)

    print "camera: uploading %s to %s:%s" % (filename, S3_BUCKET, S3_KEY)
    s3_client.meta.client.upload_file(filename, S3_BUCKET, S3_KEY)
    print "camera: upload done"

    time.sleep(1)

# returns the horizontal coordinate for a face, normalised to 100
def get_face_coordinate():
  print "get_face_coordinate: running rekognition"
  response = r_client.detect_faces(Image={'S3Object':{'Bucket':S3_BUCKET,'Name':S3_KEY}},Attributes=['ALL'])

  print "get_face_coordinate: got %d faces" % (len(response['FaceDetails']))

  face=0
  bb = None
  arr = response['FaceDetails']
  random.shuffle(arr)
  for faceDetail in arr:
    print "get_face_coordinate: face %d: age between %d and %d years old" % (face, faceDetail['AgeRange']['Low'], faceDetail['AgeRange']['High'])
    print "get_face_coordinate: face %d: gender %s/%.2f, smile %s/%.2f, glasses %s/%.2f, sunglasses %s/%.2f, beard %s/%.2f" % (face, faceDetail["Gender"]["Value"], faceDetail["Gender"]["Confidence"], faceDetail["Smile"]["Value"], faceDetail["Smile"]["Confidence"], faceDetail["Eyeglasses"]["Value"], faceDetail["Eyeglasses"]["Confidence"], faceDetail["Sunglasses"]["Value"], faceDetail["Sunglasses"]["Confidence"], faceDetail["Beard"]["Value"], faceDetail["Beard"]["Confidence"])
    if faceDetail["Eyeglasses"]["Value"] == True:
      print "get_face_coordinate: skipping face due to glasses"
      continue
    bb = faceDetail['BoundingBox']
    print "get_face_coordinate: face %d: bounding box: height=%.2f left=%.2f top=%.2f width=%.2f" % (face, bb["Height"]*100, bb["Left"]*100, bb["Top"]*100, bb["Width"]*100)
    #print json.dumps(faceDetail, indent=4, sort_keys=True)
    face += 1

  if bb == None:
    raise Exception("get_face_coordinates: no faces found")
    
  result = (bb["Left"] + bb["Width"]/2)*100
  print "get_face_coordinate: returning horizontal coordinate %.2f%% (face %d)" % (result, face)
  return result

# given a face coordinate in percent on the horizontal axis, calculate the
# angle 0-180 to move to
def get_firing_angle(face_coordinate):
  angle = 0.7053 * face_coordinate + 57.606 # determined from calibration
  print "get_firing_angle: returning angle %.2f%s for face location %.2f%%" % (angle, DEGREE, face_coordinate)
  return angle

def get_mqtt_firing_angle(face_coordinate):
  #angle = -330.1886792 * face_coordinate + 166.509434
  #angle = 1950.294861 * face_coordinate - 175.7792755
  #angle = 450 * face_coordinate + 35
  #angle = -450 * face_coordinate + 150
  #angle = 750 * face_coordinate 
  angle = -92.16270739 * face_coordinate + 153.2617299
  print "get_mqtt_firing_angle: returning angle %.2f%s for face location %.3f" % (angle, DEGREE, face_coordinate)
  return max([0, min([angle, 180])])


def shutdown():
  flag_shutdown.set()
  rest()
  camera.close()

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
  print("Connected with result code "+str(rc))

  # Subscribing in on_connect() means that if we lose the connection and
  # reconnect then subscriptions will be renewed.
  client.subscribe("/merakimv/Q2EV-2TWA-ZJDL/raw_detections")

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
  try:
    persons = map(lambda p: (p['x0'] - p['x1'])/2 + p['x0'], json.loads(msg.payload)['objects'])
    print persons
    
    global global_angle
    global_angle = get_mqtt_firing_angle(max(persons))
    global global_person
    global_person = True
    print "person at %.2f" % global_angle
    # [{u'frame': 8620, u'oid': 219, u'y1': 0.531, u'y0': 1, u'x0': 0.394, u'x1': 0.231, u'type': u'person'}]
  except:
    print "no person"
    global global_person 
    global_person = False
    return

if __name__ == "__main__":
  init()
  #gpio.set_servo_pulsewidth(GPIO_TURNTABLE, 2200)
  #gpio.write(GPIO_TRIGGER, 1)
  #gpio.write(GPIO_WARMUP, 1)
  #raise SystemExit

  client = mqtt.Client()
  client.on_connect = on_connect
  client.on_message = on_message
  
  client.connect("localhost", 1883, 60)

  ## Blocking call that processes network traffic, dispatches callbacks and
  ## handles reconnecting.
  ## Other loop*() functions are available that give a threaded interface and a
  ## manual interface.
  client.loop_start()

  # Calibration
  #angle = 90
  ##get_face_coordinate(get_image())
  #while True:
  #  t_aim = Thread(target=lambda: aim(angle, "fire"))
  #  t_aim.start()

  #  flag_fire.set()
  #  t_aim.join()
  #  lr = raw_input("Left or right?")
  #  if lr == "l":
  #    angle += 10
  #  elif lr == "r":
  #    angle -= 10
  #  else:
  #    print "Invalid input"
  #  
  #  angle = min(180, angle)
  #  angle = max(0, angle)

  #t_camera = Thread(target=lambda: get_image())
  #t_camera.start()
  t_fire = Thread(target=fire)
  t_fire.start()

  while True:
    try:
      #angle = get_firing_angle(get_face_coordinate())
      angle = global_angle
    except KeyboardInterrupt:
      print "main: shutting down"
      shutdown()
      raise SystemExit
    except Exception as e:
      # No faces found, or some other error
      print e.message
      flag_fire.clear()
      continue

    t_aim = Thread(target=lambda: aim(angle, "fire"))
    t_aim.start()

    if global_person:
      flag_fire.set()
    else:
      flag_fire.clear()

    t_aim.join()

print "main: done\n",

# get_coordinates: detected face is between 26 and 43 years old
# get_coordinates: other attributes:
# {
#     "AgeRange": {
#         "High": 43,
#         "Low": 26
#     },
#     "Beard": {
#         "Confidence": 99.8486557006836,
#         "Value": true
#     },
#     "BoundingBox": {
#         "Height": 0.4216666519641876,
#         "Left": 0.3333333432674408,
#         "Top": 0.4566666781902313,
#         "Width": 0.2800000011920929
#     },
#     "Confidence": 99.99630737304688,
#     "Emotions": [
#         {
#             "Confidence": 69.14276885986328,
#             "Type": "CALM"
#         },
#         {
#             "Confidence": 35.31903076171875,
#             "Type": "CONFUSED"
#         },
#         {
#             "Confidence": 15.290045738220215,
#             "Type": "SURPRISED"
#         }
#     ],
#     "Eyeglasses": {
#         "Confidence": 98.9239730834961,
#         "Value": false
#     },
#     "EyesOpen": {
#         "Confidence": 99.87208557128906,
#         "Value": true
#     },
#     "Gender": {
#         "Confidence": 99.92877197265625,
#         "Value": "Male"
#     },
#     "Landmarks": [
#         {
#             "Type": "eyeLeft",
#             "X": 0.42529597878456116,
#             "Y": 0.6262052655220032
#         },
#         {
#             "Type": "eyeRight",
#             "X": 0.5202105641365051,
#             "Y": 0.6153571009635925
#         },
#         {
#             "Type": "nose",
#             "X": 0.4754621088504791,
#             "Y": 0.6858598589897156
#         },
#         {
#             "Type": "mouthLeft",
#             "X": 0.4455495774745941,
#             "Y": 0.7873536944389343
#         },
#         {
#             "Type": "mouthRight",
#             "X": 0.5084024667739868,
#             "Y": 0.7814213633537292
#         },
#         {
#             "Type": "leftPupil",
#             "X": 0.42270398139953613,
#             "Y": 0.6323692798614502
#         },
#         {
#             "Type": "rightPupil",
#             "X": 0.5232028365135193,
#             "Y": 0.6199241280555725
#         },
#         {
#             "Type": "leftEyeBrowLeft",
#             "X": 0.3882981240749359,
#             "Y": 0.592139482498169
#         },
#         {
#             "Type": "leftEyeBrowUp",
#             "X": 0.41470417380332947,
#             "Y": 0.5717234015464783
#         },
#         {
#             "Type": "leftEyeBrowRight",
#             "X": 0.44457054138183594,
#             "Y": 0.5754270553588867
#         },
#         {
#             "Type": "rightEyeBrowLeft",
#             "X": 0.49848490953445435,
#             "Y": 0.5675706267356873
#         },
#         {
#             "Type": "rightEyeBrowUp",
#             "X": 0.5294485092163086,
#             "Y": 0.5571596026420593
#         },
#         {
#             "Type": "rightEyeBrowRight",
#             "X": 0.5571322441101074,
#             "Y": 0.5755150318145752
#         },
#         {
#             "Type": "leftEyeLeft",
#             "X": 0.4072261154651642,
#             "Y": 0.6296384930610657
#         },
#         {
#             "Type": "leftEyeRight",
#             "X": 0.44372332096099854,
#             "Y": 0.6251377463340759
#         },
#         {
#             "Type": "leftEyeUp",
#             "X": 0.4244607090950012,
#             "Y": 0.6159984469413757
#         },
#         {
#             "Type": "leftEyeDown",
#             "X": 0.4259525537490845,
#             "Y": 0.6352290511131287
#         },
#         {
#             "Type": "rightEyeLeft",
#             "X": 0.501408576965332,
#             "Y": 0.6175779104232788
#         },
#         {
#             "Type": "rightEyeRight",
#             "X": 0.5386224985122681,
#             "Y": 0.6155633330345154
#         },
#         {
#             "Type": "rightEyeUp",
#             "X": 0.5198421478271484,
#             "Y": 0.6047221422195435
#         },
#         {
#   # blinken                                                                               |    print "camera: removing any existing %s" % filename
#             "Type": "rightEyeDown",
#             "X": 0.5207740068435669,
#             "Y": 0.6247785687446594
#         },
#         {
#             "Type": "noseLeft",
#             "X": 0.4589848518371582,
#             "Y": 0.722411572933197
#         },
#         {
#             "Type": "noseRight",
#             "X": 0.4944266080856323,
#             "Y": 0.7170010209083557
#         },
#         {
#             "Type": "mouthUp",
#             "X": 0.47799885272979736,
#             "Y": 0.7678255438804626
#         },
#         {
#             "Type": "mouthDown",
#             "X": 0.47813594341278076,
#             "Y": 0.795036256313324
#         }
#     ],
#     "MouthOpen": {
#         "Confidence": 99.9776382446289,
#         "Value": false
#     },
#     "Mustache": {
#         "Confidence": 97.30404663085938,
#         "Value": true
#     },
#     "Pose": {
#         "Pitch": 8.72723388671875,
#         "Roll": -5.004290580749512,
#         "Yaw": 2.9776782989501953
#     },
#     "Quality": {
#         "Brightness": 38.897403717041016,
#         "Sharpness": 99.99090576171875
#     },
#     "Smile": {
#         "Confidence": 72.07964324951172,
#         "Value": true
#     },
#     "Sunglasses": {
#         "Confidence": 99.6242446899414,
#         "Value": false
#     }
# }
