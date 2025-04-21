#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import sys
import os
import argparse


def readShortcuts(baseDir, shortcutsDir, audioDir):
    result = {}
    for f in os.listdir(shortcutsDir):
        absf = os.path.join(shortcutsDir, f)
        if os.path.isdir(absf):
            if os.path.islink(absf):
                absf = os.readlink(absf)
            if not os.path.isabs(absf):
                absf = os.path.normpath(os.path.join(baseDir, audioDir, absf))
            shortcut = os.path.relpath(absf, os.path.join(baseDir, audioDir))
            result[f] = shortcut
        else:
            val = None
            if os.path.exists(absf):
                with open(absf, "r") as fobj:
                    line = fobj.read().strip()
                    if len(line.strip()) != 0:
                        val = line.rstrip()
            result[f] = val
    return result


def readFolders(audioDir, relpath=None, isFirst=True):
    result = {}
    relpath = "" if relpath is None else relpath
    hasAudioFiles = False
    for f in os.listdir(audioDir):
        absf = os.path.join(audioDir, f)
        if os.path.isfile(absf):
            if not isFirst:
                hasAudioFiles = True
        elif os.path.isdir(absf):
            childResult = readFolders(audioDir=absf, relpath=os.path.join(relpath, f), isFirst=False)
            for k, v in childResult.items():
                assert(k not in result)
                result[k] = v
    if hasAudioFiles:
        result[relpath] = os.path.exists(os.path.join(audioDir, "folder.json"))
    return result


def _deleteBrokenSymlink(shortcutsDir, cardid, d):
    i = input("\ndelete broken symlink [" + cardid + " --> " + str(d) + "]? [y/N]")
    if i == "y":
        print("deleting symlink.")
        os.remove(os.path.join(shortcutsDir, cardid))
    else:
        print("keeping broken symlink.")


def fixBrokenShortcuts(shortcutsDir, shortcuts, audioFolders):
    for cardid, dirs in shortcuts.items():
        if dirs is None:
            _deleteBrokenSymlink(shortcutsDir=shortcutsDir, cardid=cardid, d=None)
        else:
            if not dirs.startswith("cmd://") and not dirs.startswith("extcmd://") and dirs not in audioFolders:
                _deleteBrokenSymlink(shortcutsDir=shortcutsDir, cardid=cardid, d=dirs)

def _writeFolderConf(audioDir, d, content):
    with open(os.path.join(audioDir, d, "folder.json"), "w") as f:
        f.write(content)


def linkLooseFolders(shortcutsDir, audioDir, shortcuts, audioFolders, latestRFIDFile):
    allShortcutsDirs = []
    looseFolders = {}

    print("\n\n=== linking loose folders")
    for cardid, dirs in shortcuts.items():
        if not dirs.startswith("cmd://") and not dirs.startswith("extcmd://"):
            allShortcutsDirs.append(dirs)
    lc2 = 0
    for d2, hasFolderConf2 in sorted(audioFolders.items()):
        if d2 not in allShortcutsDirs:
            looseFolders[lc2] = d2
            lc2 = lc2 + 1

    while len(looseFolders) != 0:
        print("\n== loose folders:")
        for lc, d in looseFolders.items():
            print(str(lc) + ": " + d)
        selectedOption = input("\nplease select folder: ")
        if len(selectedOption.strip()) == 0:
            print("cancel.")
            break
        if not selectedOption.isnumeric():
            print("invalid input.")
            continue
        selectedOptionInt = int(selectedOption)
        if selectedOptionInt < 0 or selectedOptionInt not in looseFolders:
            print("invalid input.")
            continue

        with open(latestRFIDFile, "r") as rf:
            latestRFID = rf.read().strip()

        d = looseFolders[selectedOptionInt]
        cardid = input("\ncardid for \"" + d + "\" [" + latestRFID + "] (enter \"c\" to cancel): ")
        if cardid == "c":
            print("ok, ignoring this folder.")
        else:
            if len(cardid) == 0:
                cardid = latestRFID
            doit = True
            if cardid in shortcuts:
                doit = False
                yn = input("WARNING: cardid already assigned to " + str(shortcuts[cardid]) + ". Override? [y/N] ")
                if yn == "y":
                    doit = True

            if doit:
                with open(os.path.join(shortcutsDir, cardid), "w") as f:
                    f.write(d)
                looseFolders.pop(selectedOptionInt, None)
            else:
                print("skipping.")
    print("done.")


def findDuplicateShortcuts(shortcuts):
    print("\n\n=== Checking folders with multiple shortcuts ...")
    linkedFolders = {}
    for cardid, d in shortcuts.items():
        if d is None:
            continue
        if d not in linkedFolders:
            linkedFolders[d] = []
        linkedFolders[d].append(cardid)
    for d, cardids in linkedFolders.items():
        if len(cardids) > 1:
            print("WARNING: multiple shortcuts for folder [" + d + "]: " + str(cardids))
    print("=== done.")


if __name__ == "__main__":
    baseDir = "/home/pi/radio"
    latestRFIDFile = "/var/tmp/Latest_RFID"
    shortcutsDir = os.path.join(baseDir, "shared", "shortcuts")
    audioDir = os.path.join(baseDir, "shared", "audiofolders")

    parser = argparse.ArgumentParser()
    parser.add_argument("--baseDir", help="directory containing the phoniebox code; defaults to " + baseDir)
    parser.add_argument("--latestRFIDFile", help="file storing the latest RFID card id; defaults to " + latestRFIDFile)
    parser.add_argument("--shortcutsDir", help="directory containing the RFID card id shortcuts; defaults to " + shortcutsDir)
    parser.add_argument("--audioDir", help="directory containing the audio files; defaults to " + audioDir)

    parser.add_argument("--printShortcuts", help="print list of available shortcuts", action="store_true")
    parser.add_argument("--linkLooseFolders", help="iterate through list of folders that are currently unbound to any card id and ask user whether to link them", action="store_true")
    parser.add_argument("--fixBrokenShortcuts", help="find and delete dangling shortcuts ", action="store_true")
    parser.add_argument("--findDuplicateShortcuts", help="find and delete duplicate shortcuts ", action="store_true")
    args = parser.parse_args()

    if args.baseDir:
        baseDir = args.baseDir
    if args.latestRFIDFile:
        latestRFIDFile = args.latestRFIDFile
    if args.shortcutsDir:
        shortcutsDir = args.shortcutsDir
    if args.audioDir:
        audioDir = args.audioDir

    shortcuts = readShortcuts(baseDir=baseDir, shortcutsDir=shortcutsDir, audioDir=audioDir)
    audioFolders = readFolders(audioDir=audioDir)

    if args.printShortcuts:
        print("===== shortcuts =====")
        shortcutslist = []
        for cardid, thefolder in sorted(shortcuts.items()):
            shortcutslist.append([cardid, thefolder])
        for e in sorted(shortcutslist, key=lambda x: x[1] or ""):
            if e[1] is not None:
                print("\"" + e[1] + "\";\t\"" + e[0] + "\"")
        print("==================================")

    if args.linkLooseFolders:
        linkLooseFolders(shortcutsDir=shortcutsDir, audioDir=audioDir, shortcuts=shortcuts, audioFolders=audioFolders, latestRFIDFile=latestRFIDFile)
    if args.fixBrokenShortcuts:
        fixBrokenShortcuts(shortcutsDir=shortcutsDir, shortcuts=shortcuts, audioFolders=audioFolders)
    if args.findDuplicateShortcuts:
        shortcuts2 = readShortcuts(baseDir=baseDir, shortcutsDir=shortcutsDir, audioDir=audioDir)
        findDuplicateShortcuts(shortcuts=shortcuts2)

