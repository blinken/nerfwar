#!/usr/bin/env python
# -*- coding: utf-8 -*-

# todo:
#  * test GPIO control methods individually
#  * calibrate get_firing_angle
#  * do we want to perhaps only return to the rest position if no faces detected after some delay?

#import RPi.GPIO as GPIO
#import picamera # sudo apt-get install python-picamera

import time
import boto3
import json
from threading import Thread, Condition

# GPIO mappings
GPIO_WARMUP = 10 # active low
GPIO_TRIGGER = 11 # active low
GPIO_TURNTABLE = 12 # servo

# How long should we warmup and fire for? (seconds)
WARMUP_DELAY = 2
FIRE_TIME = 2
AIM_DELAY = 3 # how long to turn to position (TTFN is max(aim_delay, warmup_delay))

DEGREE = u'°'

#pwm = GPIO.PWM(GPIO_TURNTABLE, 100)
#pwm.start(5)

flag_aimed = Condition()
flag_fired = Condition()

def init():
  #GPIO.setmode(GPIO.BCM)

  #GPIO.setup(GPIO_WARMUP, GPIO.OUT)
  #GPIO.setup(GPIO_TRIGGER, GPIO.OUT)
  #GPIO.setup(GPIO_TURNTABLE, GPIO.OUT)

  #GPIO.output(GPIO_WARMUP, 1)
  #GPIO.output(GPIO_TRIGGER, 1)
  #GPIO.output(GPIO_TURNTABLE, 1)

  print "init: done\n",

def fire():
  flag_fired.acquire()

  print "fire: warming up\n",
  #GPIO.output(GPIO_WARMUP, 0)
  time.sleep(WARMUP_DELAY)

  print "fire: waiting for aim\n",
  flag_aimed.acquire()

  print "fire: firing\n",
  #GPIO.OUTPUT(GPIO_TRIGGER, 0)
  time.sleep(FIRE_TIME)

  print "fire: shutting down\n",
  #GPIO.OUTPUT(GPIO_TRIGGER, 1)
  #GPIO.OUTPUT(GPIO_WARMUP, 1)

  print "fire: done\n",
  flag_aimed.release()
  flag_fired.release()

# angle from 0..180
def aim(angle, reason):
  flag_aimed.acquire()
  if angle < 0 or angle > 180:
    raise Exception("Angle must be between 0%s and 180%s, got %d" % (DEGREE, DEGREE, angle))

  print "aim: moving to %.2f%s (%s)\n" % (angle, DEGREE, reason),
  duty = float(angle) / 10.0 + 2.5
  #pwm.ChangeDutyCycle(duty)
  time.sleep(AIM_DELAY)

  flag_aimed.release()

  print "aim: done (%s)\n" % (reason),

# move to the rest position
def rest():
  time.sleep(1)

  flag_fired.acquire()
  print "rest: moving to rest position\n",
  aim(180, "rest")
  flag_fired.release()

def get_image():
  # dummy
  return "/Users/patrickcoleman/rekognition/pic2.jpg"

  filename = "/tmp/nerf-input.jpg"
  print "camera: removing any existing %s" % filename
  try:
    os.remove(filename)
  except:
    pass

  camera = picamera.PiCamera()
  camera.capture(filename)

  return filename

# returns the horizontal coordinate for a face, normalised to 100
def get_face_coordinate(filename):
  bucket = 'blinken-devel'
  region = "eu-west-1"
  key = "rekognition.jpg"

  s3 = boto3.resource('s3', region)

  print "get_face_coordinate: uploading %s to %s:%s" % (filename, bucket, key)
  s3.meta.client.upload_file(filename, bucket, key)

  client=boto3.client('rekognition', region)

  print "get_face_coordinate: running rekognition"
  response = client.detect_faces(Image={'S3Object':{'Bucket':bucket,'Name':key}},Attributes=['ALL'])

  if len(response['FaceDetails']) == 0:
    raise Exception("get_face_coordinates: no faces found")

  print "get_face_coordinate: got %d faces for %s" % (len(response['FaceDetails']), filename)

  face=0
  for faceDetail in response['FaceDetails']:
    print "get_face_coordinate: face %d: age between %d and %d years old" % (face, faceDetail['AgeRange']['Low'], faceDetail['AgeRange']['High'])
    print "get_face_coordinate: face %d: gender %s/%.2f, smile %s/%.2f, sunglasses %s/%.2f, beard %s/%.2f" % (face, faceDetail["Gender"]["Value"], faceDetail["Gender"]["Confidence"], faceDetail["Smile"]["Value"], faceDetail["Smile"]["Confidence"], faceDetail["Sunglasses"]["Value"], faceDetail["Sunglasses"]["Confidence"], faceDetail["Beard"]["Value"], faceDetail["Beard"]["Confidence"])
    bb = faceDetail['BoundingBox']
    print "get_face_coordinate: face %d: bounding box: height=%.2f left=%.2f top=%.2f width=%.2f" % (face, bb["Height"]*100, bb["Left"]*100, bb["Top"]*100, bb["Width"]*100)
    #print json.dumps(faceDetail, indent=4, sort_keys=True)
    face += 1

  result = (bb["Left"] + bb["Width"]/2)*100
  print "get_face_coordinate: returning horizontal coordinate %.2f%% (face %d)" % (result, face)
  return result

# given a face coordinate in percent on the horizontal axis, calculate the
# angle 0-180 to move to
def get_firing_angle(face_coordinate):
  angle = (face_coordinate / 100) * 180
  print "get_firing_angle: returning angle %.2f%s for face location %.2f%%" % (angle, DEGREE, face_coordinate)
  return angle

if __name__ == "__main__":
  init()

  while True:
    t_rest = Thread(target=rest)
    t_rest.start()
    try:
      filename = get_image()
      angle = get_firing_angle(get_face_coordinate(filename))
    except KeyboardInterrupt:
      print "main: shutting down"
      t_rest.join()
      raise SystemExit
    except Exception as e:
      print e.message
      t_rest.join()
      continue

    t_rest.join()

    t_aim = Thread(target=lambda: aim(angle, "fire"))
    t_aim.start()
    t_fire = Thread(target=fire)
    t_fire.start()

    t_aim.join()
    t_fire.join()

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
