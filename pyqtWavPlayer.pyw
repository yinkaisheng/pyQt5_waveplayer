#!python3
# -*- coding:utf-8 -*-
#author: yinkaisheng@foxmail.compile
#this simple player uses library RawAudioSocket from http://code.google.com/p/rainforce/wiki/RawAudioSocket
from PyQt5.QtWidgets import (QApplication, QDialog, QFileDialog,
        QGridLayout, QHBoxLayout, QVBoxLayout, QMessageBox,
        QLabel, QLineEdit, QPushButton, QSpinBox)
from PyQt5.QtCore import QTimer
import pyqtAudioWriter
import math

BUTTON_HEIGHT = 30

class Dialog(QDialog):
    def __init__(self):
        super(Dialog, self).__init__()
        self.setWindowTitle('Audio Excitation(语音激励)')
        self.setFixedSize(600, 400)
        vLayout = QVBoxLayout()
        hLayout = QHBoxLayout()
        vLayout.addLayout(hLayout)
        self.gridLayout = QGridLayout()
        vLayout.addLayout(self.gridLayout)
        self.setLayout(vLayout)

        button = QPushButton('Open')
        button.setFixedHeight(BUTTON_HEIGHT)
        button.clicked.connect(self.open)
        hLayout.addWidget(button)

        button = QPushButton('Play')
        button.setFixedHeight(BUTTON_HEIGHT)
        button.clicked.connect(self.play)
        hLayout.addWidget(button)

        self.prButton = QPushButton('Pause')
        self.prButton.setFixedHeight(BUTTON_HEIGHT)
        self.prButton.clicked.connect(self.pauseOrResume)
        hLayout.addWidget(self.prButton)

        button = QPushButton('Stop')
        button.setFixedHeight(BUTTON_HEIGHT)
        button.clicked.connect(self.stop)
        hLayout.addWidget(button)

        self.label = QLabel('Select')
        vLayout.addWidget(self.label)

        self.edits = []
        self.labels = []
        self.audios = []
        self.audioValues = []
        self.timer = QTimer()
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.caculate)

    def closeEvent(self, event):
        self.stop()

    def open(self):
        fileNames, ext = QFileDialog.getOpenFileNames(self, 'Select wave or pcm files(Support multi selection)', 'E:/Media/Audio',
            'wav(*.wav);;pcm(*.pcm);;All Files(*.*)')
        if fileNames:
            self.stop()
            while True:
                item = self.gridLayout.takeAt(0)
                if item:
                    wd = item.widget()
                    if wd:
                        wd.deleteLater()
                else:
                    break
            self.edits.clear()
            self.labels.clear()
            self.audioValues.clear()
            for (i, it) in enumerate(fileNames):
                label = QLabel('wave {0}'.format(i+1))
                self.gridLayout.addWidget(label, i, 0)
                edit = QLineEdit(it)
                self.edits.append(edit)
                self.gridLayout.addWidget(edit, i, 1)
                label = QLabel('value')
                label.setFixedWidth(80)
                self.labels.append(label)
                self.gridLayout.addWidget(label, i, 2)
                self.audioValues.append([0,0])

    def play(self):
        for it in self.audios:
            if it.isPlaying():
                return
        self.audios.clear()
        for it in self.edits:
            audio = pyqtAudioWriter.AudioWriter()
            audio.UpdateUI.connect(self.updateUI)
            audio.open(it.text())
            audio.start()
            self.audios.append(audio)
        self.timer.start()

    def pauseOrResume(self):
        if self.audios and self.audios[0].isPlaying():
            btnText = self.prButton.text()
            if btnText == 'Pause':
                self.pause()
            elif btnText == 'Resume':
                self.resume()

    def pause(self):
        for it in self.audios:
            it.pause()
        self.prButton.setText('Resume')

    def resume(self):
        for it in self.audios:
            it.resume()
        self.prButton.setText('Pause')

    def stop(self):
        self.resume()
        for it in self.audios:
            it.stop()
        # self.audios.clear() # del sender cause error, can't del sender in its slot
        self.timer.stop()
        for it in self.audioValues:
            it[0] = 0
            it[1] = 0

    def updateUI(self, value):
        index = self.audios.index(self.sender())
        if value:
            db = 20 * math.log10(value / (1<<15))
            self.labels[index].setText('{0:<5} {1:.1f}db'.format(value, db))
        else:
            self.labels[index].setText('{0:<5}'.format(value))
        self.audioValues[index][0] += value
        self.audioValues[index][1] += 1

    def caculate(self):
        index, max = 0, 0
        for (i, it) in enumerate(self.audioValues):
            if it[1]:
                average = it[0]/it[1]
                it[0] = 0
                it[1] = 0
                if max < average:
                    max = average
                    index = i
        self.label.setText('select {0}'.format(index+1))
        for it in self.audios:
            if it.isPlaying():
                return
        self.timer.stop()

if __name__ == '__main__':
    import sys
    app = QApplication(sys.argv)
    dialog = Dialog()
    ret = dialog.exec_()
    sys.exit(ret)
