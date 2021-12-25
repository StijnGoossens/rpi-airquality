# Monitor airquality with Raspberry Pi and a VMA342 (= CCS811 + BME280) and MH-Z19 sensor

Outputs per sensor:
- MH-Z19 -> CO2
- VMA342:
    - CCS811 -> TVOC
    - BME280 -> temperature + humidity + air pressure


## General Raspberry Pi setup

### Install OS and activate SSH
1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Insert micro SD card and install the Raspberry Pi OS using Raspberry Pi Imager.
3. Add “SSH” File to the SD Card Root (`touch /Volumes/boot/ssh`).
4. Insert the SD card, connect your computer and Pi with an ethernet cable and boot the Pi by plugging in the power cord.
5. Connect with the Pi via SSH: `ssh pi@raspberrypi.local`, default password = `raspberry`.

### Connect the RPi to WiFi

The following steps will allow you to SSH into your Raspberry Pi over Wifi instead of over ethernet.

On the Pi, edit the `wpa_supplicant.conf` file by adding your Wifi networks ssid and password as shown below:

1. `sudo nano /etc/wpa_supplicant/wpa_supplicant.conf`

Add the following content:

```
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=be                                           

network={
    ssid="my-ssid"
    psk="my-password"
}
```

2. `sudo reboot`
3. Check the WiFi connection: `ifconfig wlan0` An IP address should be visible if the connection was successful.


## Project specific setup
Install the packages below in order to be able to interact with the sensors:

### CCS811
*⚠️ STILL WIP, BECAUSE OF BUG WHEN READING OUT THE VALUES⚠️*

- `pip install wheel` (not sure whether really required)

- Install RPi.GPIO with `export CFLAGS=-fcommon` and `pip3 install RPi.GPIO` ([source](https://raspberrypi.stackexchange.com/questions/119632/ubuntu-20-10-and-gpio))

- `pip3 install adafruit-circuitpython-ccs811`

- Enable I2C via `sudo raspi-config` ([source](https://raspberrypi.stackexchange.com/questions/66145/raspberry-pi-3-not-detecting-i2c-device)) + optionally adding the I2C module to the kernel (see source)

- Reduce the baudrate in order to make the sensor compatible with Raspberry Pi: `sudo nano /boot/config.txt and add dtparam=i2c_arm_baudrate=10000 (source)`

Example to try out the CCS811 library:
```python
import board
import adafruit_ccs811

i2c = board.I2C()  # uses board.SCL and board.SDA
ccs811 = adafruit_ccs811.CCS811(i2c, 0x5b)

# Wait for the sensor to be ready
while not ccs811.data_ready:
    pass

while True:
    print("CO2: {} PPM, TVOC: {} PPB".format(ccs811.eco2, ccs811.tvoc))
    time.sleep(0.5)
```
Note that `0x5b` is the I2C address of the CCS811 on the VMA342 board ([default is `0x5a`](https://github.com/adafruit/Adafruit_CircuitPython_CCS811/blob/main/adafruit_ccs811.py)).

### BME280

## Hardware

