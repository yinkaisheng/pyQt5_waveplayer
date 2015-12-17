#!python3
# -*- coding:utf-8 -*-
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
import socket

DEBUG = True # False
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

class AudioWriter():
    def __init__(self):
        self.hwaveout = HWAVEOUT()
        self.wavefx = WAVEFORMATEX(
            WAVE_FORMAT_PCM,
            2,     # nChannels
            44100, # SamplesPerSec
            176400,# AvgBytesPerSec = 44100 SamplesPerSec * 4 nBlockAlign
            4,     # nBlockAlign = 2 nChannels * 16 wBitsPerSample / 8 bits per byte
            16,    # wBitsPerSample
            0
        )
        # For gapless playback, we schedule two audio blocks at a time, each
        # block with its own header
        self.headers = [WAVEHDR(), WAVEHDR()]

        #: configurable size of chunks (data blocks) read from input stream
        self.BUFSIZE = 100 * 2**10
        self.BYTESPERSEC = 176400  # needed to calculate buffer playback time

    def open(self):
        """ 1. Open default wave device, tune it for the incoming data flow
        """
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

        debug( "Default Wave Audio output device is opened successfully" )

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

    def play(self, stream):
        """Read PCM audio blocks from stream and write to the output device

             `stream` is anything with .read() method. Playback stops if read
             operation returned 0 bytes
        """

        blocknum = len(self.headers) #: number of audio data blocks to be queued
        curblock = 0      #: start with block 0
        stopping = False  #: stopping playback when no input
        prevlen  = 0      #: previously read length to detect buffer underruns
        divideBase = 1.05
        while True:
            debug("dwFlags 0:{0}, 1:{1}".format(self.headers[0].dwFlags, self.headers[1].dwFlags))
            freeids = [x for x in range(blocknum)
                                     if self.headers[x].dwFlags in (0, WHDR_DONE)]
            if (len(freeids) == blocknum) and stopping:
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
                    stopping = True
                    break
                if prevlen < self.BUFSIZE and readlen < self.BUFSIZE:
                    debug("  underrun warn - read %s/%s (%d%%) of buffer size" %
                                (readlen, self.BUFSIZE, readlen*100//self.BUFSIZE))
                shortArray = array.array('h') # int16
                shortArray.frombytes(data)
                debug("block max num                 {0}".format(max(shortArray)))
                self._schedule_block(data, self.headers[i])

            debug("waiting for block %d" % curblock)

            # waiting until buffer playback is finished by constantly polling
            # its status eats 100% CPU time. this counts how many checks are made
            pollsnum = 0
            # avoid 100% CPU usage - with this pollsnum won't be greater than 1
            time.sleep(readlen/divideBase/float(self.BYTESPERSEC)) # must devide a number greater that 1, make pollsnum greater than 1, otherwise there will be a gap between them

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

    def close(self):
        """ x. Close Sound Device """
        winmm.waveOutClose(self.hwaveout)
        debug( "Default Wave Audio output device is closed" )

#-- /CHAPTER 1 --

#-- CHAPTER 2: READING STREAM FROM THE SOCKET --

class SocketStream(object):
    """ Convert network socket connection to a readable stream object """
    def __init__(self, host='localhost', port=44100):
        """Wait until there is a connection"""
        # [ ] listening socket blocks keyboard input, so CtrlC/CtrlBreak
        #     will not work until a new connection is established
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        # [ ] only one client served at a time
        sock.listen(0)
        self.conn, self.addr = sock.accept()
        self.sock = sock

    def read(self, size):
        return self.conn.recv(size)

    def close(self):
        self.conn.close()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except:
            pass
        self.sock.close()


if __name__ == '__main__':
    print("--- Local file playback example ---")
    aw = AudioWriter()
    aw.open()
    
    # PCM 16bit, little endian, signed, 44.1kHz, stereo, left interleaved
    with open('e:\\Media\\Audio\\qianqian44100.wav', 'rb') as stream:
        aw.play(stream)

    aw.close()


    print("--- Playback from TCP port :44100 ---")
    print("To feed an audio stream with netcat, execute:")
    print("  nc -v localhost 44100 < sample.raw")

    aw = AudioWriter()
    aw.open()

    while True:      
        stream = SocketStream(host='')
        print("got signal from %s:%s" % stream.addr)
        aw.play(stream)
        stream.close()

    aw.close()

