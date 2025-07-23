# rfid-music-player: RFID based music player, fast as lightning
Similar to the phoniebox.de project - but more lightweight.

**Boot time on a raspberry pi zero 2: ~30 seconds**

## how to install
- install dependencies
```
sudo apt-get install mpd mpc python3-mpd python3-evdev
```



- checkout the repository (e.g., on a raspberry pi, run the following command as user "pi"):
```
git clone https://github.com/mtill/rfid-music-player.git
```

- setup config.json configuration file:
```
cp config-template.json config.json
```
Now, update the settings from config.json accordingly. As a minimum, you should set "rfidReaderNames" and include the name of your RFID USB reader. You may use the list-devices.py script to identify connected USB devices.

- add an USB flash drive (e.g., formatted with NTFS file system)
```
mkdir /mnt/usb/music
mkdir /mnt/usb/playlists
```

- create mount script /etc/mount-usb.sh:
```
fsck /dev/sda1
mount /dev/sda1 /mnt/usb -o defaults,auto,nofail
```

- make that script executable:
```
chmod a+x /etc/mount-usb.sh
```

- allow user "pi" to run mount-usb.sh without password by adding the following to /etc/sudoers:
```
pi ALL=(ALL) NOPASSWD: /etc/mount-usb.sh
```

- configure mpd
  set the following entries in /etc/mpd.conf:
```
password "YOUR_MPD_PASSWORD@read,add,control,admin"
follow_outside_symlinks "yes"
follow_inside_symlinks "yes"
auto_update	"yes"
bind_to_address "0.0.0.0"
music_directory "/mnt/usb/music"
playlist_directory "/mnt/usb/playlists"
```

- link your music folder
```
cd PATH_TO_RFID_MUSIC_PLAYER
cd shared
ln -s /mnt/usb/music audiofolders
```

- enable auto-start
  when using a raspberry pi, you can enable auto-login for user "pi" via raspi-config; then, add the following to /home/pi/.bashrc:
```
sudo /etc/mount-usb.sh
mpc -h YOUR_MPD_PASSWORD@localhost update
cd RFID_REPO_PATH
./radio.py >/var/tmp/radio-err.log 2>&1 &
```

- enable read-only overlay by running raspi-config and enabling overlay (can be found in the "performance" submenu)


## how to use

### how to add music
On the USB flash drive, map RFID codes to the music folders by appending the RFID code to the folder names, by using "-" as a separator.
As an example, if you have a folder named "party songs for children" and you'd like to map it to RFID card 00012345, then rename that folder to, e.g., "party songs for children-00012345" (important: don't forget the "-"!).
It's not important how exactly you're going to name that folder, as long as the exact RFID code is part of the folder name.
You can assign multiple RFID cards to one folder, e.g., like this: "party songs for children-00012345-00054321".

### how to add audiobooks
This works analogously to adding music files. For audiobooks, however, you usually want to resume listening on the latest playback position. To enable auto-resume, create a folder "audiobooks" on your USB flash drive and in that folder, create a file "folder.json" with the following content:
```
{
  "resume": true
}
```

### how to add radio streams
You can also add radio streams; to do so, create an empty folder, and inside this folder, create a file "folder.json" with the following data:
```
{
  "type": "playlist-stream",
  "uri": "http://play.rockantenne.de/rockantenne.m3u"
}
```

(example for adding the stream provided by Rock Antenne radio).

Or, if you have an MP3 stream, use the following example:
```
{
  "type": "stream",
  "uri": "https://live.streams.klassikradio.de/klassikradio-deutschland/stream/mp3"
}
```
(example for Klassik Radio Deutschland)

### how to configure infrared devices (optional)
On your raspberry pi, enable IR via /boot/firmware/config.txt. Example:
```
dtoverlay=gpio-ir,gpio_pin=4
```

Folders can then be mapped to numbers entered via infrared remote by renaming folders to, e.g., "party songs for children-lirc1" (please note the mandatory prefix "lirc"). Then, when pressing "1 + KEY\_OK" on your infrared remote, the content of the folder is being played.


### how to control your music box from your mobile phone
On Android, simply install one of the MPD client apps, e.g., M.A.L.P. .


