# rfid-music-player
RFID based music player, fast as lightning

Similar to the phoniebox.de project - but more lightweight.


## how to install
- checkout the repository (e.g., on a raspberry pi, run the following command as user "pi"):
```
git clone https://github.com/mtill/rfid-music-player.git
```

- setup config.json configuration file:
```
cp config-template.json config.json
```

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
cd audiofolders
ln -s /mnt/usb/music audiofolders
```

- enable auto-start
  when using a raspberry pi, you can enable auto-login for user "pi" via raspi-config; then, add the following to /home/pi/.bashrc:
```
sudo /etc/mount-usb.sh
mpc -h YOUR_MPD_PASSWORD@localhost update
cd RFID_REPO_PATH
./radio.py \>/var/tmp/radio-err.log 2>&1 &
```

- enable read-only overlay by running raspi-config and enabling overlay (can be found in the "performance" submenu)


## how to use
On the USB flash drive, map RFID codes to the music folders by appending the RFID code to the folder names.
As an example, if you have a folder named "party songs for children" and you'd like to map it to RFID card 00012345, then rename that folder to, e.g., "party songs for children-00012345".
It's not important how exactly you're going to name that folder, as long as the exact RFID code is part of the folder name.


