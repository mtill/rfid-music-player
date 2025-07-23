#!/usr/bin/env python3
# coding=utf-8


import logging
#logfilename = '/var/tmp/radio-' + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + '.log'
logfilename = '/var/tmp/radio.log'
logging.basicConfig(filename=logfilename, filemode='w', level=logging.INFO)


import time
import datetime
import json
from pathlib import Path
import subprocess
import threading
from contextlib import contextmanager
from collections import deque

import evdev
from mpd import MPDClient
from RFIDReader import RFIDReader


# Recursive function to iterate through all files in a directory and its subdirectories
# alternatively, you can use `pathlib.Path.rglob('**/*', recurse_symlinks=True)` to achieve the same result, but recurse_symlinks is only available in Python 3.13+
def _iterdir_recursive(path: Path, dirsonly=False):
    queue = deque([path])
    visited = []

    while queue:
        current_path = queue.popleft()
        current_path_abs = current_path.resolve().as_posix()
        if current_path_abs in visited:
            continue
        visited.append(current_path_abs)

        # traverse files first
        thedirs = []
        thefiles = []
        for p in current_path.iterdir():
            if p.is_dir():
                thedirs.append(p)
            elif not dirsonly:
                thefiles.append(p)

        for r in sorted(thefiles, key=lambda x: x.name.lower()) + sorted(thedirs, key=lambda x: x.name.lower()):
            yield r
            if r.is_dir():
                queue.append(r)


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
    def __init__(self, dir_path: Path, volumeSteps, minVolume, maxVolume, muteTimeoutS, doSavePos, alsaAudioDevice, doUpdateBeforePlaying):
        self.dir_path = dir_path
        self.volumeSteps = volumeSteps
        self.minVolume = minVolume
        self.maxVolume = maxVolume
        self.muteTimeoutS = muteTimeoutS
        self.doSavePos = doSavePos
        self.alsaAudioDevice = alsaAudioDevice
        self.doUpdateBeforePlaying = doUpdateBeforePlaying

        self.currentFolder = None
        self.currentFolderConf = None
        self.recordProcess = None
        self.aplayProcess = None
        self.thetimer = None

        self.soundEffects = {}
        self.audiofolder = Path("shared", "audiofolders")
        self.shortcutsfolder = Path("shared", "shortcuts")
        self.absRecordingsDir = dir_path / self.audiofolder / "Recordings"

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
            absFolder = self.dir_path / self.audiofolder / self.currentFolder
            if self.currentFolderConf is not None and self.currentFolderConf.get("resume", False) and absFolder.exists():
                currentStatus = client.status()
                lastPos = {
                    "song": currentStatus.get("song", None),
                    "elapsed": currentStatus.get("elapsed", None)
                }

                lastPosFile = absFolder / "lastPos.json"
                try:
                    with open(lastPosFile, "w") as f:
                        json.dump(lastPos, f)
                except:
                    logging.error('failed to write lastPos: ' + self.currentFolderConf["uri"])
                    return False
        return True

    def playEntry(self, client, relpath):
        logging.info('playEntry: ' + str(relpath))
        self._stopAlsaProcesses()
        self.savePos(client=client)

        absFile = self.dir_path / self.audiofolder / relpath
        if not absFile.exists():
            logging.error("file does not exist: " + str(absFile))
            return

        absFolderRel = self.dir_path / self.audiofolder

        folderConf = {}
        for r in relpath.parts:
            absFolderRel = absFolderRel / r
            folderConfFile = absFolderRel / "folder.json"
            if folderConfFile.exists():
                with open(folderConfFile, "r") as folderConfFileObj:
                    folderConf |= json.load(folderConfFileObj)

        client.clear()
        client.single(0)
        client.repeat(0)
        client.add(relpath)
        client.play(0)


    def playFolder(self, client, relfolder):
        logging.info('playFolder: ' + str(relfolder))
        self._stopAlsaProcesses()
        self.savePos(client=client)

        absFolder = self.dir_path / self.audiofolder / relfolder
        absFolderRel = self.dir_path / self.audiofolder

        folderConf = {}
        for r in relfolder.parts:
            absFolderRel = absFolderRel / r
            folderConfFile = absFolderRel / "folder.json"
            if folderConfFile.exists():
                with open(folderConfFile, "r") as folderConfFileObj:
                    folderConf |= json.load(folderConfFileObj)

        self.currentFolder = relfolder
        self.currentFolderConf = folderConf

        client.clear()
        folderType = folderConf.get("type", "music")
        theuri = folderConf.get("uri", None)
        if theuri is not None and theuri.startswith("./"):
            theuri = relfolder / theuri[2:]

        if folderType in ["music"]:
            if self.doUpdateBeforePlaying:
                client.update(relfolder)
                while True:
                    update_status = client.status().get("updating_db", None)
                    if update_status is None or len(update_status) == 0:
                        break
                    time.sleep(0.5)

            client.add(relfolder.as_posix())

        elif folderType in ["stream"]:
            if theuri is not None:
                client.add(theuri)

        elif folderType in ["playlist", "playlist-stream"]:
            if theuri is not None:
                client.load(theuri)

        else:
            logging.info("unknown folder type: " + folderType)
            return

        client.single(0)
        client.repeat(1)

        if folderConf.get("resume", False):
            lastPosFile = absFolder / "lastPos.json"
            lastPos = None
            try:
                if lastPosFile.exists():
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

    def playSingleFile(self, client, relSoundFile: Path, useAplay=False, repeat=False):
        if useAplay:
            if repeat:
                raise Exception("repeat only works if useAplay=False")
            self.pause(client=client)
            self._stopAlsaProcesses()
            thefile = self.dir_path / relSoundFile
            self.aplayProcess = subprocess.Popen(["/usr/bin/aplay", "-D", self.alsaAudioDevice, str(thefile)], close_fds=True)   # running in background
        else:
            self._stopAlsaProcesses()
            self.savePos(client=client)
            client.stop()
            client.clear()
            client.single(1)
            rrepeat = 1 if repeat else 0
            client.repeat(rrepeat)
            client.add(relSoundFile.as_posix())
            client.play(0)

    def record(self, client, durationInSeconds):
        if self._isRecording():
            return False

        self._stopAlsaProcesses()
        self.pause(client=client)
        timestr = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        thefile = self.absRecordingsDir / (timestr + ".wav")
        self.recordProcess = subprocess.Popen(["/usr/bin/arecord", "-D", self.alsaAudioDevice, "--duration=" + str(durationInSeconds), "-f", "cd", "-vv", str(thefile)], close_fds=True)   # running in background
        return True

    def playLastRecord(self, client):
        self.pause(client=client)
        for absFile in sorted(self.absRecordingsDir.iterdir(), key=lambda ii: ii.stat().st_mtime, reverse=True):
            if absFile.is_file() and absFile.name.lower().endswith(".wav"):
                self.playSingleFile(client=client, relSoundFile=absFile, useAplay=True)
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

def resolveShortcut(dir_path: Path, shortcutsfolder, audiofolder, cardid):
    shortcutPrefix = None
    shortcut = None
    cardpath = dir_path / shortcutsfolder / cardid

    if cardpath.exists():
        shortcutPrefix = "folder"

        if cardpath.is_file():
            with open(cardpath, "r") as f:
                shortcut_content = f.read().strip()

            shortcutPrefixPos = shortcut_content.find("://")
            if shortcutPrefixPos != -1:
                shortcutPrefix = shortcut_content[:shortcutPrefixPos]
                shortcut = shortcut_content[shortcutPrefixPos + 3:]

        else:
            abspath = cardpath
            if abspath.is_symlink():
                abspath = abspath.readlink()
            if not abspath.is_absolute():
                abspath = (dir_path / audiofolder / abspath).resolve()
            shortcut = str((dir_path / audiofolder).relative_to(abspath))

    else:
        af = Path(audiofolder)
        for c in _iterdir_recursive(af, dirsonly=True):   #af.glob("**/"):
            if cardid in c.name.split("-"):
                shortcutPrefix = "folder"
                shortcut = str(c.relative_to(af))
                break

    if shortcut is None:
        logging.info("ignoring cardid " + cardid)

    return shortcut, shortcutPrefix

def playAction(dir_path: Path, player, connection, cardid):
    player.updateTimer(connection=connection)

    shortcut, shortcutPrefix = resolveShortcut(dir_path=dir_path, shortcutsfolder=player.shortcutsfolder, audiofolder=player.audiofolder, cardid=cardid)
    if shortcut is None or shortcutPrefix is None:
        return None

    if shortcutPrefix == "cmd":
        cmdAction(player=player, connection=connection, actionstring=shortcut)
        return shortcut
    elif shortcutPrefix == "extcmd":
        subprocess.call(shortcut, shell=True)
        return shortcut
    elif shortcutPrefix == "folder":
        if shortcut == player.currentFolder:
            cmdAction(player=player, connection=connection, actionstring="continue-or-next")
            return "continue-or-next"

        absFolder = player.dir_path / player.audiofolder / shortcut
        if absFolder.exists():
            with connection.getConnectedClient() as client:
                player.playFolder(client=client, relfolder=Path(shortcut))
            return "playfolder"

    return None


class lircThread(threading.Thread):
    def __init__(self, dir_path: Path, player, connection, lircDevice, lockKeys, unlockKeys, toggleLockKeys, lircLocked, prefix="lirc"):
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
        self.prefix = prefix
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
                                playAction(dir_path=self.dir_path, player=self.player, connection=self.connection, cardid=self.prefix+jumpval)
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
    def __init__(self, dir_path: Path, reader, player, connection, sameCardDelay, latestRFIDFile, lockCardIDs, unlockCardIDs, toggleLockCardIDs, rfidLocked, prefix=""):
        threading.Thread.__init__(self)
        self.dir_path = dir_path
        self.reader = reader
        self.player = player
        self.connection = connection
        self.sameCardDelay = sameCardDelay
        self.latestRFIDFile = latestRFIDFile
        self.lockCardIDs = lockCardIDs
        self.unlockCardIDs = unlockCardIDs
        self.toggleLockCardIDs = toggleLockCardIDs
        self.prefix = prefix

        self.isUp = False
        self.isLocked = rfidLocked

    def run(self):
        defaultCardDelay = self.sameCardDelay.get("default", 0)
        previous_performedAction = None
        previous_id = ""
        previous_time = 0

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

                    thisCardDelay = self.sameCardDelay.get(previous_performedAction, defaultCardDelay)
                    if cardid == previous_id and (time.time() - previous_time) < float(thisCardDelay):
                        logging.debug('Ignoring card due to sameCardDelay')
                    else:
                        previous_performedAction = playAction(dir_path=self.dir_path, player=self.player, connection=self.connection, cardid=self.prefix+cardid)
                        previous_id = cardid
                        previous_time = time.time()

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
    dir_path = Path(__file__).resolve().parent
    logging.info('dir_path: ' + str(dir_path))

    with open(dir_path / "config.json", "r") as f:
        config = json.load(f)

    connection = MPDConnection(host=config["host"], port=config["port"], pwd=config.get("pwd", None))
    inputDevices = [evdev.InputDevice(path) for path in evdev.list_devices()]

    player = MusicPlayer(dir_path=dir_path,
                         volumeSteps=config.get("volumeSteps", 5),
                         minVolume=config.get("minVolume", None),
                         maxVolume=config.get("maxVolume", None),
                         muteTimeoutS=config.get("muteTimeoutS", None),
                         doSavePos=config.get("savePos", True),
                         alsaAudioDevice=config.get("alsaAudioDevice", "default"),
                         doUpdateBeforePlaying=config.get("updateBeforePlaying", True))

    inputThreads = []
    if config.get("rfidReaderNames", None) is not None:
        for rfidReaderName in config["rfidReaderNames"]:
            reader = RFIDReader(rfidReaderName=rfidReaderName)
            inputThreads.append(rfidThread(dir_path=dir_path, reader=reader, player=player, connection=connection,
                                        sameCardDelay=config.get("sameCardDelay", None),
                                        latestRFIDFile=Path(config["latestRFIDFile"]),
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
            player.playFolder(client=client, relfolder=Path(startupfolder))
        elif startupsound is not None:
            player.playSingleFile(client=client, relSoundFile=Path(startupsound))

    for t2 in inputThreads:
        t2.join()

