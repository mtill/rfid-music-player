#!/usr/bin/env python3
# coding=utf-8


import logging
#logfilename = '/var/tmp/radio-' + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + '.log'
logfilename = '/var/tmp/radio.log'
logging.basicConfig(filename=logfilename, filemode='w', level=logging.INFO)

RESUME_FOLDERNAMES = ["Audiobooks", "Podcasts", "Hörbücher", "Kinder-Hörbücher"]

import time
import datetime
import json
import os
from pathlib import Path
import subprocess
import threading
from contextlib import contextmanager

import evdev
from mpd import MPDClient
from RFIDReader import RFIDReader


# Recursive function to iterate through all files in a directory and its subdirectories
# alternatively, you can use `pathlib.Path.rglob('**/*', recurse_symlinks=True)` to achieve the same result, but recurse_symlinks is only available in Python 3.13+
def _iterdir_recursive(path: Path):
    for p in path.iterdir():
        if p.is_dir():
            yield from _iterdir_recursive(p)
        else:
            yield p


class MPDConnection():
    def __init__(self, host, port, pwd, closeAfterSeconds=7):
        self.client = MPDClient()
        self.client.timeout = 100
        self.client.idletimeout = 100

        self.host = host
        self.port = port
        self.pwd = pwd
        self.closeAfterSeconds = closeAfterSeconds

        self.lock = threading.Lock()
        self.thetimer = None
        self.clientConnected = False

    def _closeConnection(self):
        with self.lock:
            if self.clientConnected:
                self.client.close()
                self.client.disconnect()
                self.clientConnected = False

    @contextmanager
    def getConnectedClient(self):
        if self.thetimer is not None:
            self.thetimer.cancel()

        try:
            with self.lock:
                if not self.clientConnected:
                    self.client.connect(self.host,self.port)
                    self.clientConnected = True
                    if self.pwd is not None:
                        self.client.password(self.pwd)
                yield self.client
        finally:
            self.thetimer = threading.Timer(self.closeAfterSeconds, self._closeConnection)
            self.thetimer.start()


class MusicPlayer():
    def __init__(self, dir_path, serverAudioPath, volumeSteps, minVolume, maxVolume, muteTimeoutS, doSavePos, doUpdateBeforePlaying):
        self.dir_path = dir_path
        self.serverAudioPath = serverAudioPath
        self.volumeSteps = volumeSteps
        self.minVolume = minVolume
        self.maxVolume = maxVolume
        self.muteTimeoutS = muteTimeoutS
        self.doSavePos = doSavePos
        self.doUpdateBeforePlaying = doUpdateBeforePlaying

        self.currentFolder = None
        self.currentFolderConf = None
        self.recordProcess = None
        self.aplayProcess = None
        self.thetimer = None

        self.soundEffects = {}
        self.audiofolder = os.path.join("shared", "audiofolders")
        self.shortcutsfolder = os.path.join("shared", "shortcuts")
        self.relRecordingsDir = "Recordings"
        self.absRecordingsDir = os.path.join(dir_path, self.audiofolder, self.relRecordingsDir)

    def _isRecording(self):
        return self.recordProcess is not None and self.recordProcess.poll() is None

    def stopRecording(self):
        if self._isRecording():
            self.recordProcess.kill()
            return True
        return False

    def _stopAlsaProcesses(self):
        self.stopRecording()
        if self.aplayProcess is not None and self.aplayProcess.poll() is None:
            self.aplayProcess.kill()

    def savePos(self, client):
        if not self.doSavePos:
            return False

        if self.currentFolder is not None:
            absFolder = os.path.join(self.dir_path, self.audiofolder, self.currentFolder)
            if self.currentFolderConf is not None and self.currentFolderConf.get("resume", False) and os.path.exists(absFolder):
                currentStatus = client.status()
                lastPos = {
                    "song": currentStatus.get("song", None),
                    "elapsed": currentStatus.get("elapsed", None)
                }

                lastPosFile = os.path.join(absFolder, "lastPos.json")
                try:
                    with open(lastPosFile, "w") as f:
                        json.dump(lastPos, f)
                except:
                    logging.error('failed to write lastPos: ' + self.currentFolderConf["uri"])
                    return False
        return True

    def playFolder(self, client, relfolder):
        logging.info('playFolder: ' + relfolder)
        self._stopAlsaProcesses()
        self.savePos(client=client)

        absFolder = os.path.join(self.dir_path, self.audiofolder, relfolder)

        relfoldersplit = os.path.split(relfolder)
        absFolderRel = os.path.join(self.dir_path, self.audiofolder)
        folderConf = {}
        for r in relfoldersplit:
            absFolderRel = os.path.join(absFolderRel, r)
            folderConfFile = os.path.join(absFolderRel, "folder.json")
            if os.path.exists(folderConfFile):
                with open(folderConfFile, "r") as folderConfFileObj:
                    folderConf |= json.load(folderConfFileObj)

        for rfs in relfolder.split("/"):
            if rfs in RESUME_FOLDERNAMES:
                folderConf["resume"] = True
                break

        self.currentFolder = relfolder
        self.currentFolderConf = folderConf

        client.clear()
        folderType = folderConf.get("type", "music")
        theuri = folderConf.get("uri", None)
        if theuri is not None and theuri.startswith("./"):
            if relfolder.endswith("/"):
                theuri = relfolder + theuri[2:]
            else:
                theuri = relfolder + "/" + theuri[2:]

        if folderType in ["music"]:

            if self.doUpdateBeforePlaying:
                client.update(relfolder)
                while True:
                    update_status = client.status().get("updating_db", None)
                    if update_status is None or len(update_status) == 0:
                        break
                    time.sleep(0.5)

            client.add(relfolder)
        elif folderType in ["stream"]:
            if theuri is not None:
                client.add(folderConf["uri"])
        elif folderType in ["playlist", "playlist-stream"]:
            if theuri is not None:
                client.load(theuri)
        else:
            logging.info("unknown folder type: " + folderType)
            return

        client.single(0)
        client.repeat(1)

        if folderConf.get("resume", False):
            lastPosFile = os.path.join(absFolder, "lastPos.json")
            lastPos = None
            try:
                if os.path.exists(lastPosFile):
                    with open(lastPosFile, "r") as lastPosFileObj:
                        lastPos = json.load(lastPosFileObj)
            except:
                logging.error("failed to parse lastPos.json")
            song = 0
            elapsed = None
            if lastPos is not None:
                song = lastPos.get("song", 0)
                elapsed = lastPos.get("elapsed", None)
            if elapsed is None:
                client.play(song)
            else:
                client.seek(song, elapsed)
        else:
            client.play(0)

    def jumpTo(self, client, pos):
        self._stopAlsaProcesses()
        client.play(pos)

    def playNext(self, client):
        self._stopAlsaProcesses()
        client.next()

    def playPrevious(self, client):
        self._stopAlsaProcesses()
        client.previous()

    def increaseVolume(self, client):
        self._stopAlsaProcesses()
        curVol = int(client.status().get("volume", 0))
        if self.maxVolume is None or curVol + self.volumeSteps <= self.maxVolume:
            client.volume(self.volumeSteps)

    def decreaseVolume(self, client):
        self._stopAlsaProcesses()
        curVol = int(client.status().get("volume", 0))
        if self.minVolume is None or curVol - self.volumeSteps >= self.minVolume:
            client.volume(self.volumeSteps * -1)

    def shuffle(self, client):
        self._stopAlsaProcesses()
        client.shuffle()

    def seek(self, client, reltimeS):
        if reltimeS == 0:
            return

        r = reltimeS
        if reltimeS >= 0:
            r = "+" + str(reltimeS)

        client.seekcur(r)

    def pause(self, client, val=1):
        self._stopAlsaProcesses()
        if val is None:
            client.pause()
        else:
            client.pause(val)
        self.savePos(client=client)

    def play(self, client):
        self._stopAlsaProcesses()
        client.play()

    def playSingleFile(self, client, relSoundFile, useAplay=False, repeat=False):
        if useAplay:
            if repeat:
                raise Exception("repeat only works if useAplay=False")
            self.pause(client=client)
            self._stopAlsaProcesses()
            thefile = os.path.join(self.dir_path, relSoundFile)
            self.aplayProcess = subprocess.Popen(["/usr/bin/aplay", thefile], close_fds=True)   # running in background
        else:
            self._stopAlsaProcesses()
            self.savePos(client=client)
            client.stop()
            client.clear()
            client.single(1)
            rrepeat = 1 if repeat else 0
            client.repeat(rrepeat)
            client.add(relSoundFile)
            client.play(0)

    def record(self, client, durationInSeconds):
        if self._isRecording():
            return False

        self._stopAlsaProcesses()
        self.pause(client=client)
        timestr = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        thefile = os.path.join(self.absRecordingsDir, timestr + ".wav")
        self.recordProcess = subprocess.Popen(["/usr/bin/arecord", "-D", "default", "--duration=" + str(durationInSeconds), "-f", "cd", "-vv", thefile], close_fds=True)   # running in background
        return True

    def playLastRecord(self, client):
        self.pause(client=client)
        for i in sorted(Path(self.absRecordingsDir).iterdir(), key=os.path.getmtime, reverse=True):
            absFile = os.path.join(self.absRecordingsDir, i)
            if os.path.isfile(absFile) and absFile.endswith(".wav"):
                self.playSingleFile(client=client, relSoundFile=os.path.join(self.relRecordingsDir, i), useAplay=True)
                return

    def updateDB(self, client, uri=None):
        if uri is None:
            client.update()
            #client.rescan()
        else:
            client.update(uri)
            #client.rescan(uri)

    def sync(self, connection):

        waitmusic = self.soundEffects.get("wait", None)
        if waitmusic is not None:
            with connection.getConnectedClient() as client:
                self.playSingleFile(client=client, relSoundFile=waitmusic, repeat=True)

        try:
            subprocess.call(["./sync-this-phoniebox.sh"], shell=True)
        except Exception as e:
            logging.error('Execution of ./sync-this-phoniebox.sh failed: {e}'.format(e=e))

        with connection.getConnectedClient() as client:
            self.updateDB(client=client)

        while True:
            time.sleep(2)
            with connection.getConnectedClient() as client:
                if client.status().get("updating_db", None) is None:
                    donemusic = self.soundEffects.get("done", None)
                    if donemusic is not None:
                        self.playSingleFile(client=client, relSoundFile=donemusic)
                    else:
                        self.pause(client=client)
                    break

    def _muteTimeout(self, connection):
        with connection.getConnectedClient() as client:
            self.pause(client=client)

    def updateTimer(self, connection):
        if self.muteTimeoutS is None:
            return
        if self.thetimer is not None:
            self.thetimer.cancel()
        self.thetimer = threading.Timer(self.muteTimeoutS, self._muteTimeout, args=[connection])
        self.thetimer.start()


def cmdAction(player, connection, actionstring):
    logging.info("cmd action: " + actionstring)

    if actionstring == "sync":
        player.sync(connection=connection)
    else:
        with connection.getConnectedClient() as client:
            if actionstring == "pause":
                player.pause(client=client)
            elif actionstring == "togglepause":
                player.pause(client=client, val=None)
            elif actionstring == "next":
                player.playNext(client=client)
            elif actionstring == "continue-or-next":
                if client.status().get("state", None) == "play":
                    player.playNext(client=client)
                else:
                    player.pause(client=client, val=0)
            elif actionstring == "previous":
                player.playPrevious(client=client)    
            elif actionstring == "volumeup":
                player.increaseVolume(client=client)
            elif actionstring == "volumedown":
                player.decreaseVolume(client=client)
            elif actionstring == "shuffle":
                player.shuffle(client=client)
            elif actionstring == "updateDB":
                player.updateDB(client=client)
            elif actionstring == "record300s":
                player.record(client=client, durationInSeconds=300)
            elif actionstring == "playLastRecord":
                player.playLastRecord(client=client)
            elif actionstring == "seek+10":
                player.seek(client=client, reltimeS=10)
            elif actionstring == "seek-10":
                player.seek(client=client, reltimeS=-10)
            elif actionstring == "playstartupsound":
                startupsound = player.soundEffects.get("startup", None)
                if startupsound is not None:
                    player.playSingleFile(client=client, relSoundFile=startupsound)
            elif actionstring == "ignore":
                logging.info("action: ignore.")
            else:
                logging.info("unknown cmd action: " + actionstring)

def resolveShortcut(dir_path, shortcutsfolder, audiofolder, cardid):
    shortcut = None
    shortcutPrefix = None
    cardpath = os.path.join(dir_path, shortcutsfolder, cardid)

    if os.path.exists(cardpath):
        shortcutPrefix = "folder"

        if os.path.isfile(cardpath):
            with open(cardpath, "r") as f:
                shortcut = f.read().strip()

            if shortcut.startswith("cmd://"):
                shortcutPrefix = "cmd://"
            elif shortcut.startswith("extcmd://"):
                shortcutPrefix = "extcmd://"

        else:
            abspath = cardpath
            if os.path.islink(abspath):
                abspath = os.readlink(abspath)
            if not os.path.isabs(abspath):
                abspath = os.path.normpath(os.path.join(dir_path, audiofolder, abspath))
            shortcut = os.path.relpath(abspath, os.path.join(dir_path, audiofolder))
    else:
        af = Path(audiofolder)
        for c in _iterdir_recursive(af):   #af.glob("**/"):
            if c.name.find(cardid) != -1:
                shortcutPrefix = "folder"
                shortcut = str(c.relative_to(af))
                break

    if shortcut is None:
        logging.info("ignoring cardid " + cardid)

    return shortcut, shortcutPrefix

def playAction(dir_path, player, connection, cardid):
    player.updateTimer(connection=connection)

    shortcut, shortcutPrefix = resolveShortcut(dir_path=dir_path, shortcutsfolder=player.shortcutsfolder, audiofolder=player.audiofolder, cardid=cardid)
    if shortcut is None or shortcutPrefix is None:
        return None

    if shortcutPrefix == "cmd://":
        cmdAction(player=player, connection=connection, actionstring=shortcut[6:])
        return shortcut
    elif shortcutPrefix == "extcmd://":
        subprocess.call(shortcut[9:], shell=True)
        return shortcut

    if shortcut == player.currentFolder:
        cmdAction(player=player, connection=connection, actionstring="continue-or-next")
        return "continue-or-next"

    absFolder = os.path.join(player.dir_path, player.audiofolder, shortcut)
    if os.path.exists(absFolder):
        with connection.getConnectedClient() as client:
            player.playFolder(client=client, relfolder=shortcut)
    return "playfolder"


class lircThread(threading.Thread):
    def __init__(self, dir_path, player, connection, lircDevice, lockKeys, unlockKeys, toggleLockKeys, lircLocked):
        threading.Thread.__init__(self)
        self.dir_path = dir_path
        self.player = player
        self.connection = connection
        self.lircDevice = lircDevice
        self.lockKeys = lockKeys
        self.unlockKeys = unlockKeys
        self.toggleLockKeys = toggleLockKeys

        self.isUp = False
        self.isLocked = lircLocked
        self.keynums = {'KEY_1': 1,
                        'KEY_2': 2,
                        'KEY_3': 3,
                        'KEY_4': 4,
                        'KEY_5': 5,
                        'KEY_6': 6,
                        'KEY_7': 7,
                        'KEY_8': 8,
                        'KEY_9': 9,
                        'KEY_0': 0}

    def _getSeekSeconds(self, duration):
        return round(pow((3.0 * duration), 2), 1)

    def run(self):
        jumpval = ""
        jumptime = 0

        lastcode = 0
        lastkeydowntime = 0

        self.isUp = True
        while self.isUp:
            try:
                for event in self.lircDevice.read_loop():
                    if event.type != evdev.ecodes.EV_KEY or event.code not in evdev.ecodes.KEY:
                        continue

                    # up: 0   down: 1   (hold: 2)
                    duration = 0.0
                    if event.value == 0:
                        if lastcode == event.code:
                            duration = time.time() - lastkeydowntime

                        lastcode = 0
                        lastkeydowntime = 0

                    elif event.value == 1:
                        lastcode = event.code
                        lastkeydowntime = time.time()
                        continue
                    else:
                        continue

                    chlist = evdev.ecodes.KEY[event.code]
                    if not isinstance(chlist, list):
                        chlist = [chlist]

                    if time.time() - jumptime > 5:
                        jumpval = ""
                        jumptime = 0

                    for ch in chlist:

                        if self.lockKeys is not None and ch in self.lockKeys:
                            self.isLocked = True
                            continue
                        if self.unlockKeys is not None and ch in self.unlockKeys:
                            self.isLocked = False
                            continue
                        if self.toggleLockKeys is not None and ch in self.toggleLockKeys:
                            self.isLocked = not self.isLocked
                            continue

                        if self.isLocked:
                            continue

                        if ch in self.keynums:
                            jumpval = jumpval + str(self.keynums[ch])
                            jumptime = time.time()
                        else:

                            if ch == "KEY_OK" and jumptime != 0:   # some number has been entered before
                                playAction(dir_path=self.dir_path, player=self.player, connection=self.connection, cardid=jumpval)
                            elif ch in ["KEY_CHANNELDOWN", "KEY_LEFT"] and duration >= 1:
                                with self.connection.getConnectedClient() as client:
                                    self.player.seek(client=client, reltimeS=-self._getSeekSeconds(duration=duration))
                            elif ch in ["KEY_CHANNELUP", "KEY_RIGHT"] and duration >= 1:
                                with self.connection.getConnectedClient() as client:
                                    self.player.seek(client=client, reltimeS=self._getSeekSeconds(duration=duration))
                            elif ch in ["KEY_CHANNELUP", "KEY_CHANNELDOWN", "KEY_RIGHT", "KEY_LEFT"] and jumptime != 0:   # some number has been entered before
                                with self.connection.getConnectedClient() as client:
                                    self.player.jumpTo(client=client, pos=int(jumpval))
                            else:
                                playAction(dir_path=self.dir_path, player=self.player, connection=self.connection, cardid=ch)

                            jumpval = ""
                            jumptime = 0
                            duration = 0

            except Exception as e:
                logging.error('Execution failed: {e}'.format(e=e))
                time.sleep(2)

    def stop(self):
        self.isUp = False


class rfidThread(threading.Thread):
    def __init__(self, dir_path, reader, player, connection, sameCardDelay, jumpcards, latestRFIDFile, lockCardIDs, unlockCardIDs, toggleLockCardIDs, rfidLocked):
        threading.Thread.__init__(self)
        self.dir_path = dir_path
        self.reader = reader
        self.player = player
        self.connection = connection
        self.sameCardDelay = sameCardDelay
        self.jumpcards = jumpcards
        self.latestRFIDFile = latestRFIDFile
        self.lockCardIDs = lockCardIDs
        self.unlockCardIDs = unlockCardIDs
        self.toggleLockCardIDs = toggleLockCardIDs

        self.isUp = False
        self.isLocked = rfidLocked

    def run(self):
        defaultCardDelay = self.sameCardDelay.get("default", 0)
        previous_performedAction = None
        previous_id = ""
        previous_time = 0
        jumpval = ""
        jumpcount = 0
        jumptime = 0

        self.isUp = True
        while self.isUp:
            try:
                time.sleep(0.2)

                #cardid = input("card id: ")
                cardid = self.reader.readCard()

                if cardid is not None:
                    with open(self.latestRFIDFile, "w") as latestRFIDFileObj:
                        latestRFIDFileObj.write(cardid)

                    if self.lockCardIDs is not None and cardid in self.lockCardIDs:
                        self.isLocked = True
                        continue
                    if self.unlockCardIDs is not None and cardid in self.unlockCardIDs:
                        self.isLocked = False
                        continue
                    if self.toggleLockCardIDs is not None and cardid in self.toggleLockCardIDs:
                        self.isLocked = not self.isLocked
                        continue

                    if self.isLocked:
                        continue

                    if cardid in self.jumpcards:
                        previous_time = 0
                        if time.time() - jumptime > 5:
                            jumpval = ""
                            jumpcount = 0
                            jumptime = 0

                        jumpval = jumpval + str(self.jumpcards[cardid])
                        jumpcount = jumpcount + 1
                        jumptime = time.time()

                        if jumpcount == 3:
                            #with self.connection.getConnectedClient() as client:
                            #    self.player.jumpTo(client=client, pos=int(jumpstring))
                            previous_id = jumpval
                            previous_performedAction = playAction(dir_path=self.dir_path, player=self.player, connection=self.connection, cardid=previous_id)
                            previous_time = time.time()
                            jumpval = ""
                            jumpcount = 0
                            jumptime = 0

                    else:
                        thisCardDelay = self.sameCardDelay.get(previous_performedAction, defaultCardDelay)
                        if cardid == previous_id and (time.time() - previous_time) < float(thisCardDelay):
                            logging.debug('Ignoring card due to sameCardDelay')
                        else:
                            previous_performedAction = playAction(dir_path=self.dir_path, player=self.player, connection=self.connection, cardid=cardid)
                            previous_id = cardid
                            previous_time = time.time()

                        jumpval = ""
                        jumpcount = 0
                        jumptime = 0

            except Exception as e:
                logging.error('Execution failed: {e}'.format(e=e))
                time.sleep(2)

    def stop(self):
        self.isUp = False


def getInputDevice(inputDevices, name):
    for i in inputDevices:
        if i.name == name:
            return i
    return None


if __name__ == "__main__":
    dir_path = os.path.dirname(os.path.realpath(__file__))
    logging.info('dir_path: ' + dir_path)

    with open(os.path.join(dir_path, "config.json"), "r") as f:
        config = json.load(f)

    connection = MPDConnection(host=config["host"], port=config["port"], pwd=config.get("pwd", None))
    inputDevices = [evdev.InputDevice(path) for path in evdev.list_devices()]

    serverAudioPath = config.get("serverAudioPath", None)
    player = MusicPlayer(dir_path=dir_path,
                         serverAudioPath=serverAudioPath,
                         volumeSteps=config.get("volumeSteps", 5),
                         minVolume=config.get("minVolume", None),
                         maxVolume=config.get("maxVolume", None),
                         muteTimeoutS=config.get("muteTimeoutS", None),
                         doSavePos=config.get("savePos", True),
                         doUpdateBeforePlaying=config.get("updateBeforePlaying", False))

    inputThreads = []
    if config.get("rfidReaderNames", None) is not None:
        reader = RFIDReader(deviceNames=config["rfidReaderNames"])
        inputThreads.append(rfidThread(dir_path=dir_path, reader=reader, player=player, connection=connection,
                                    sameCardDelay=config.get("sameCardDelay", None),
                                    jumpcards=config.get("jumpcards", {}),
                                    latestRFIDFile=config["latestRFIDFile"],
                                    lockCardIDs=config.get("lockCardIDs", None),
                                    unlockCardIDs=config.get("unlockCardIDs", None),
                                    toggleLockCardIDs=config.get("toggleLockCardIDs", None),
                                    rfidLocked=config.get("rfidLocked", False)))
    if config.get("lirc", False):
        lircDevice = getInputDevice(inputDevices=inputDevices, name=config["lircdevice"])
        if lircDevice is None:
            logging.error("IR device not found: " + config["lircdevice"])
        else:
            logging.info("found IR device: " + str(lircDevice.path + " " + lircDevice.name + " " + lircDevice.phys))
            inputThreads.append(lircThread(dir_path=dir_path, player=player, connection=connection,
                                        lircDevice=lircDevice,
                                        lockKeys=config.get("lockKeys", None),
                                        unlockKeys=config.get("unlockKeys", None),
                                        toggleLockKeys=config.get("toggleLockKeys", None),
                                        lircLocked=config.get("lircLocked", False)))

    with connection.getConnectedClient() as client:
        #client.enableoutput(0)
        client.clear()
        if "initialVolume" in config:
            client.setvol(config["initialVolume"])

        client.update()   # or: client.rescan() ?
        for t in inputThreads:
            t.start()

        startupfolder = config.get("startupfolder", None)
        player.soundEffects = config.get("soundEffects", {})
        startupsound = player.soundEffects.get("startup", None)
        if startupfolder is not None:
            player.playFolder(client=client, relfolder=startupfolder)
        elif startupsound is not None:
            player.playSingleFile(client=client, relSoundFile=startupsound)

    for t2 in inputThreads:
        t2.join()

