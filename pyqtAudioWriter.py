"""
Implementation of Raw Audio Socket server spec in pure Python
http://code.google.com/p/rainforce/wiki/RawAudioSocket

Public domain work by anatoly techtonik <techtonik@gmail.com>
Use MIT License if public domain doesn't make sense for you.

'sample.raw' is 'frequency_change_approved.wav' air traffic
control phrase recorded by corsica_s through walkie-talkie.


Change History:

0.1 - proof of concept, loads and plays entire data file in
            one turn, uses predefined sleep interval of one second to
            avoid 100% CPU usage when checking if playback is complete
0.2 - loads data piece by piece, plays with noticeable lags due
            to the absence of buffering, 100% CPU usage, because sleep
            interval is undefined
0.3 - organize code into AudioWriter class
0.4 - playback lag is killed by double buffering, still 100% CPU
            usage because of constant polling to check for processed
            blocks
0.5 - remove 100% CPU usage by sleeping while a block is playing
0.6 - Python 3 compatibility
0.7 - socket stream playback, buffer underrun detection (not
            exposed in API), still Windows only

Usage:

Just execute .py file for a demo, and look at the end of the source
code too see how it is used as a library.
"""

import sys
import array
import time
import threading
from PyQt5.QtCore import QObject, pyqtSignal

DEBUG = False
def debug(msg):
    if DEBUG:
        print("debug: %s" % msg)

#-- CHAPTER 1: CONTINUOUS SOUND PLAYBACK WITH WINDOWS WINMM LIBRARY --
#
# Based on tutorial "Playing Audio in Windows using waveOut Interface"
# by David Overton

import ctypes
from ctypes import wintypes

winmm = ctypes.windll.winmm

# --- define necessary data structures from mmsystem.h

# 1. Open Sound Device

HWAVEOUT = wintypes.HANDLE
WAVE_FORMAT_PCM = 0x1
WAVE_MAPPER = -1
MMSYSERR_NOERROR = 0
WAV_HEADER_SIZE = 44

class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ('wFormatTag',  wintypes.WORD),
            # 0x0001    WAVE_FORMAT_PCM. PCM audio
            # 0xFFFE    The format is specified in the WAVEFORMATEXTENSIBLE.SubFormat
            # Other values are in mmreg.h
        ('nChannels',   wintypes.WORD),
        ('SamplesPerSec',  wintypes.DWORD),
        ('AvgBytesPerSec', wintypes.DWORD),
            # for WAVE_FORMAT_PCM is the product of nSamplesPerSec and nBlockAlign
        ('nBlockAlign', wintypes.WORD),
            # for WAVE_FORMAT_PCM is the product of nChannels and wBitsPerSample
            # divided by 8 (bits per byte)
        ('wBitsPerSample', wintypes.WORD),
            # for WAVE_FORMAT_PCM should be equal to 8 or 16
        ('cbSize',      wintypes.WORD)]
            # extra format information size, should be 0

# Data must be processes in pieces that are multiple of
# nBlockAlign bytes of data at a time. Written and read
# data from a device must always start at the beginning
# of a block. Playback of PCM data can not be started in
# the middle of a sample on a non-block-aligned boundary.

CALLBACK_NULL = 0

# 2. Write Audio Blocks to Device

PVOID = wintypes.HANDLE
WAVERR_BASE = 32
WAVERR_STILLPLAYING = WAVERR_BASE + 1
class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ('lpData', wintypes.LPSTR), # pointer to waveform buffer
        ('dwBufferLength', wintypes.DWORD),  # in bytes
        ('dwBytesRecorded', wintypes.DWORD), # when used in input
        ('dwUser', wintypes.DWORD),          # user data
        ('dwFlags', wintypes.DWORD),  # various WHDR_* flags set by Windows
        ('dwLoops', wintypes.DWORD),  # times to loop, for output buffers only
        ('lpNext', PVOID),            # reserved, struct wavehdr_tag *lpNext
        ('reserved', wintypes.DWORD)] # reserved
# The lpData, dwBufferLength, and dwFlags members must be set before calling
# the waveInPrepareHeader or waveOutPrepareHeader function. (For either
# function, the dwFlags member must be set to zero.)
WHDR_DONE = 1  # Set by the device driver for finished buffers
# --- /define ----------------------------------------


# -- Notes on double buffering scheme to avoid lags --
#
# Windows maintains a queue of blocks sheduled for playback.
# Any block passed through the waveOutPrepareHeader function
# is inserted into the queue with waveOutWrite.

class AudioWriter(QObject, threading.Thread):
    UpdateUI = pyqtSignal(int)

    def __init__(self):
        super(AudioWriter, self).__init__()
        self._isPlaying = False
        self.stopping = False
        self.playEvent = threading.Event()
        self.playEvent.set()
        self.lockPlay = threading.Lock()
        self.lockStop = threading.Lock()
        self.hwaveout = HWAVEOUT()
        self.wavefx = WAVEFORMATEX(
            WAVE_FORMAT_PCM,
            2,     # nChannels
            44100, # SamplesPerSec
            176400,# AvgBytesPerSec = 44100 *4, SamplesPerSec * one sample bytes(2*2=4)
            4,     # nBlockAlign = 2 nChannels * 16 wBitsPerSample / 8 bits per byte
            16,    # wBitsPerSample
            0
        )
        # For gapless playback, we schedule two audio blocks at a time, each
        # block with its own header
        self.headers = [WAVEHDR(), WAVEHDR()]

        #: configurable size of chunks (data blocks) read from input stream
        self.BUFSIZE = 40*2**10
        self.BYTESPERSEC = 176400  # needed to calculate buffer playback time

    def open(self, file):
        """ 1. Open default wave device, tune it for the incoming data flow
        """
        self.file = file
        ret = winmm.waveOutOpen(
            ctypes.byref(self.hwaveout), # buffer to receive a handle identifying
                                                            # the open waveform-audio output device
            WAVE_MAPPER,            # constant to point to default wave device
            ctypes.byref(self.wavefx),   # identifier for data format sent for device
            0, # DWORD_PTR dwCallback - callback function
            0, # DWORD_PTR dwCallbackInstance - user instance data for callback
            CALLBACK_NULL  # DWORD fdwOpen - flag for opening the device
        )

        if ret != MMSYSERR_NOERROR:
            sys.exit('Error opening default waveform audio device (WAVE_MAPPER)')

        # volume = 10|(10<<16)
        # winmm.waveOutSetVolume(self.hwaveout, volume)
        debug( "Default Wave Audio output device is opened successfully" )

    def isPlaying(self):
        isPlaying = False
        self.lockPlay.acquire()
        isPlaying = self._isPlaying
        self.lockPlay.release()
        return isPlaying

    def pause(self):
        self.playEvent.clear()

    def resume(self):
        self.playEvent.set()

    def stop(self):
        self.lockStop.acquire()
        self.stopping = True
        self.lockStop.release()

    def _schedule_block(self, data, header):
        """Schedule PCM audio data block for playback. header parameter
             references free WAVEHDR structure to be used for scheduling."""
        header.dwBufferLength = len(data)
        header.lpData = data

        # Prepare block for playback
        if winmm.waveOutPrepareHeader(
                 self.hwaveout, ctypes.byref(header), ctypes.sizeof(header)
             ) != MMSYSERR_NOERROR:
            sys.exit('Error: waveOutPrepareHeader failed')

        # Write block, returns immediately unless a synchronous driver is
        # used (not often)
        if winmm.waveOutWrite(
                 self.hwaveout, ctypes.byref(header), ctypes.sizeof(header)
             ) != MMSYSERR_NOERROR:
            sys.exit('Error: waveOutWrite failed')

    def run(self):
        """Read PCM audio blocks from stream and write to the output device

             `stream` is anything with .read() method. Playback stops if read
             operation returned 0 bytes
        """
        stream = open(self.file, 'rb')
        if self.file.lower().endswith('.wav'):
            stream.seek(WAV_HEADER_SIZE, 0) # skip wave header
        blocknum = len(self.headers) #: number of audio data blocks to be queued
        curblock = 0      #: start with block 0
        stopping = False  #: stopping playback when no input
        prevlen  = 0      #: previously read length to detect buffer underruns
        divideBase = 1.05
        maxValue = 0
        self.lockPlay.acquire()
        self._isPlaying = True
        self.lockPlay.release()
        while True:
            self.playEvent.wait()
            debug("dwFlags 0:{0}, 1:{1}".format(self.headers[0].dwFlags, self.headers[1].dwFlags))
            freeids = [x for x in range(blocknum)
                                     if self.headers[x].dwFlags in (0, WHDR_DONE)]
            self.lockStop.acquire()
            stopping = self.stopping
            self.lockStop.release()
            if stopping: # (len(freeids) == blocknum)
                break
            debug("empty blocks %s" % freeids)

            # Fill audio queue
            for i in freeids:
                if stopping:
                    break
                debug("scheduling block %d" % i)
                data = stream.read(self.BUFSIZE)
                readlen = len(data)
                if readlen == 0:
                    self.stop()
                    break
                if prevlen < self.BUFSIZE and readlen < self.BUFSIZE:
                    debug("  underrun warn - read %s/%s (%d%%) of buffer size" %
                                (readlen, self.BUFSIZE, readlen*100//self.BUFSIZE))
                shortArray = array.array('h') # int16
                shortArray.frombytes(data)
                maxValue = max(shortArray)
                debug("block max num    {0}".format(maxValue))
                self.UpdateUI.emit(maxValue)
                self._schedule_block(data, self.headers[i])

            debug("waiting for block %d" % curblock)

            # waiting until buffer playback is finished by constantly polling
            # its status eats 100% CPU time. this counts how many checks are made
            pollsnum = 0
            # avoid 100% CPU usage
            waitTime = readlen/divideBase/float(self.BYTESPERSEC) # approximately time, must devide a number greater that 1, make pollsnum greater than 1, otherwise there will be a gap between them
            time.sleep(waitTime) # must devide a number greater that 1, make pollsnum greater than 1, otherwise there will be a gap between them

            while True:
                pollsnum += 1
                # unpreparing the header fails until the block is played
                ret = winmm.waveOutUnprepareHeader(
                                self.hwaveout,
                                ctypes.byref(self.headers[curblock]),
                                ctypes.sizeof(self.headers[curblock])
                            )
                if ret == WAVERR_STILLPLAYING:
                    continue
                if ret != MMSYSERR_NOERROR:
                    sys.exit('Error: waveOutUnprepareHeader failed with code 0x%x' % ret)
                debug("dwFlags {0}:{1}".format(curblock, self.headers[curblock].dwFlags))
                break
            debug("  %s check(s)" % pollsnum)
            if pollsnum == 1:
                divideBase += 0.01

            # Switch waiting pointer to the next block
            curblock = (curblock + 1) % len(self.headers)
            prevlen = readlen
        stream.close()
        self.stopping = False
        self.lockPlay.acquire()
        self._isPlaying = False
        self.lockPlay.release()

    def close(self):
        """ x. Close Sound Device """
        winmm.waveOutClose(self.hwaveout)
        debug( "Default Wave Audio output device is closed" )

