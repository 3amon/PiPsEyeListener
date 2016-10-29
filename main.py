#!/usr/bin/python

# open a microphone in pyAudio and listen for taps

import pyaudio
import struct
import math
import wave
import datetime
import array
import sys
import subprocess
import os

INITIAL_THRESHOLD = 0.010
FORMAT = pyaudio.paInt16
FRAME_MAX_VALUE = 2 ** 15 - 1
NORMALIZE = (1.0 / FRAME_MAX_VALUE)
CHANNELS = 1
RATE = 16000
INPUT_BLOCK_TIME = 0.05
INPUT_FRAMES_PER_BLOCK = int(RATE*INPUT_BLOCK_TIME)

# if we get this many noisy blocks in a row, increase the threshold
OVERSENSITIVE = 10.0/INPUT_BLOCK_TIME

# if we get this many quiet blocks in a row, decrease the threshold
UNDERSENSITIVE = 10.0/INPUT_BLOCK_TIME

# 1 second of sound is necessary for us to care
SOUND_FILTER_LEN = 1.0/INPUT_BLOCK_TIME

NORMALIZE_MINUS_ONE_dB = 10 ** (-1.0 / 20)

# Our long moving average
LONG_AVG_LEN = 60.0 / INPUT_BLOCK_TIME

# Our short moving average
SHORT_AVG_LEN = 5.0 / INPUT_BLOCK_TIME

# Server scp key
SERVER_KEY = "server.pem"

SERVER_PATH = os.environ['AUDIO_LISTENER_PATH']

def get_rms( block ):

    # iterate over the block.
    sum_squares = 0.0
    for sample in block:
        # sample is a signed short in +/- 32768.
        # normalize it to 1.0
        n = sample * NORMALIZE
        sum_squares += n*n

    return math.sqrt( sum_squares / len(block) )

class ExpMovAvg(object):

    def __init__(self, length):
        self.length = length
        self.count = 0
        self.avg = 0.0

    def average(self):
        if(self.ready()):
            return self.avg
        else:
            raise Exception("Moving average not ready!")

    def ready(self):
        return self.count > self.length

    def add_value(self, point):
        if(self.ready()):
            self.avg = (self.avg * (self.length - 1) + point) / self.length
        else:
            self.count += 1
            self.avg = (self.avg * (self.count - 1) + point) / self.count


class AudioLogger(object):
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.errorcount = 0
        self.buffer = array.array('h')
        self.short_avg = ExpMovAvg(SHORT_AVG_LEN)
        self.long_avg = ExpMovAvg(LONG_AVG_LEN)
        self.start()


    def start(self):
        self.stream = self.open_mic_stream()
        self.recording = False
        self.lookbackcache = []

    def stop(self):
        self.stream.close()

    def find_input_device(self):
        device_index = None
        for i in range( self.pa.get_device_count() ):
            devinfo = self.pa.get_device_info_by_index(i)
            print( "Device %d: %s"%(i,devinfo["name"]) )

            for keyword in ["usb"]:
                if keyword in devinfo["name"].lower():
                    print( "Found an input: device %d - %s"%(i,devinfo["name"]) )
                    device_index = i
                    return device_index

        if device_index == None:
            print( "No preferred input found; using default input device." )

        return device_index

    def open_mic_stream( self ):
        device_index = self.find_input_device()

        stream = self.pa.open(format = FORMAT,
                              channels = CHANNELS,
                              rate = RATE,
                              input = True,
                              input_device_index = device_index,
                              frames_per_buffer = INPUT_FRAMES_PER_BLOCK)

        return stream

    def write_file(self, suffix, frames):
        fmt = '{fname}_%Y-%m-%d-%H-%M-%S.wav'
        fileName = datetime.datetime.now().strftime(fmt).format(fname=suffix)
        waveFile = wave.open(fileName, 'wb')
        waveFile.setnchannels(CHANNELS)
        waveFile.setsampwidth(self.pa.get_sample_size(FORMAT))
        waveFile.setframerate(RATE)
        data = struct.pack('<' + ('h' * len(frames)), *frames)
        waveFile.writeframes(data)
        waveFile.close()
        print "Wrote :", fileName
        return fileName

    def send_file(self, fileName):
        subprocess.Popen(["scp", "-i", SERVER_KEY, fileName, SERVER_PATH]).wait()

    def listen(self):
        try:
            data_chunk = array.array('h', self.stream.read(INPUT_FRAMES_PER_BLOCK))
            if sys.byteorder == 'big':
                data_chunk.byteswap()
        except IOError, e:
            # dammit.
            self.errorcount += 1
            print( "(%d) Error recording: %s"%(self.errorcount,e) )
            self.noisycount = 1
            self.stop()
            self.start()
            return False

        amplitude = get_rms( data_chunk )
        self.long_avg.add_value(amplitude)
        self.short_avg.add_value(amplitude)

        self.lookbackcache.append((data_chunk, amplitude))
        while len(self.lookbackcache) > SHORT_AVG_LEN:
            self.lookbackcache.pop(0)

        if(self.long_avg.ready() and self.short_avg.ready() and len(self.lookbackcache) == SHORT_AVG_LEN):
            if(self.short_avg.average() > self.long_avg.average() * 1.1):
                if not self.recording:
                    print "Recording started!"
                    self.recording = True
                    self.buffer = array.array('h')
                    # We need to dump the samples that started getting loud
                    loud_chunk_found = False
                    for (data_chunk, amplitude) in self.lookbackcache:
                        if loud_chunk_found or amplitude > self.long_avg.average() * 1.1:
                            loud_chunk_found = True
                            self.buffer.extend(data_chunk)
                else:
                    # keep adding sound data while we are still significantly louder than the long avg
                    self.buffer.extend(data_chunk)
            elif self.recording:
                # Recording stopped
                self.stop()
                self.recording = False
                fileName = self.write_file("event", self.buffer)
                self.send_file(fileName)
                self.start()
                os.remove(fileName)
                self.lookbackcache = []
        return True

if __name__ == "__main__":
        tt = AudioLogger()
        while(True):
            tt.listen()
