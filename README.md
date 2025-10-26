# Air quality monitoring with Raspberry Pi

In this project, we'll monitor several parameters of indoor air quality with a Raspberry Pi and the following sensors:
- MH-Z19 -> **CO2**
- VMA342, consisting of:
    - BME280 -> **temperature** + **humidity** + **air pressure**
    - CCS811 -> volatile organic compounds (**TVOC**) [Work in progress]

<img src="images/rpi-and-sensors.jpg" height="500" />
<br/>
A Streamlit dashboard will allow you to monitor the current air quality as well as the evolution over time:
<table>
    <tr>
        <td><img src="images/dashboard_example_1.png" height="600"/></td>
        <td><img src="images/dashboard_example_2.png" height="600"/></td>
    </tr>
</table>

## Requirements
- Raspberry Pi<sup>*</sup>, including:
    - MicroSD card
    - Micro USB power cable and adapter
    - Protective case
    - WiFi dongle (for RPi's older than model 3)
- [VMA342 sensor](https://www.velleman.eu/products/view?id=450324)
- [MH-Z19 sensor](https://www.hobbyelectronica.nl/product/mh-z19b-co2-sensor/)<sup>**</sup>
- Small breadboard
- Jumper cables (male-female)
- Ethernet cable for the initial setup

<sup>*</sup>I used a Raspberry Pi model 2B for this project. Other models likely work as well, but weren't tested.

<sup>**</sup>The MH-Z19 sensor comes in [multiple versions](https://emariete.com/en/sensor-co2-mh-z19b/). I used the MH-Z19B. The [mh-z19 python library](https://pypi.org/project/mh-z19/) seems to support at least the MH-Z19 and MH-Z19B. If your sensor doesn't come with output pins (like mine), you'll have to solder some stacking headers yourself.

## General Raspberry Pi setup

### Install OS and activate SSH
1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Insert micro SD card and install the Raspberry Pi OS using Raspberry Pi Imager. Before starting the instalation process, select "Edit Settings" when asked to customize the OS image. In the settings menu, make sure to:
    - Set your host name, username and password (for this guide, we assume `raspberrypi.local`, `pi` and `raspberry` respectively)
    - Enable SSH (with password authentication)
    - Configure Wifi
3. Insert the SD card and boot your Raspberry Pi by plugging in the power cord. Give it a few minutes to complete the setup.
4. Connect with the Pi via your terminal: `ssh pi@raspberrypi.local`, default password = `raspberry`.

## Wiring the hardware

Connect the Raspberry Pi and MH-Z19 as follows:
<table>
    <tr>
        <th>RPi</th>
        <th>MH-Z19</th>
    </tr>
    <tr>
        <td>5V</td>
        <td>Vin</td>
    </tr>
    <tr>
        <td>GND</td>
        <td>GND</td>
    </tr>
    <tr>
        <td>TXD</td>
        <td>Rx</td>
    </tr>
    <tr>
        <td>RXD</td>
        <td>Tx</td>
    </tr>
</table>

Note how the TXD and RXD are cross connected between the RPi and MH-Z19.

Connect the Raspberry Pi and VMA342 as follows:
<table>
    <tr>
        <th>RPi</th>
        <th>VMA342</th>
    </tr>
    <tr>
        <td>3V3</td>
        <td>3.3V</td>
    </tr>
    <tr>
        <td>GND</td>
        <td>GND</td>
    </tr>
    <tr>
        <td>SDA</td>
        <td>SDA</td>
    </tr>
    <tr>
        <td>SCL</td>
        <td>SCL</td>
    </tr>
    <tr>
        <td>GND</td>
        <td>WAKE</td>
    </tr>
</table>

<img src="images/electrical-connection-scheme.png" height="600" />

## Project specific setup
Clone this repository in the Documents folder:
`cd Documents`
`git clone https://github.com/StijnGoossens/rpi-airquality.git`

Subsequently, install the packages below in order to interact with the sensors.

### MH-Z19 (CO2)

- Enable Serial via `sudo raspi-config` ([source](https://github.com/UedaTakeyuki/mh-z19/wiki/How-to-Enable-Serial-Port-hardware-on-the-Raspberry-Pi))
- Grant your user access to the serial device so `mh-z19` can read `/dev/serial0`: `sudo adduser $USER dialout` (log out and back in afterward).

- Install SWIG once so the `lgpio` dependency can build: `sudo apt install swig`
- Install the native GPIO library used at link time: `sudo apt install liblgpio-dev`
- Install core scientific packages via apt so Python wheels do not need to compile from source on the Raspberry Pi:
    - `sudo apt install python3-numpy python3-pandas python3-dev libopenblas-dev liblapack-dev gfortran`
    - On older Raspberry Pi OS releases (Bullseye/Bookworm) you can install `libatlas-base-dev` instead of `libopenblas-dev liblapack-dev`.

- Install the [mh-z19 package](https://pypi.org/project/mh-z19/) inside a Python virtual environment to avoid Raspberry Pi OS's system package guard:

```bash
python3 -m venv --system-site-packages ~/venvs/airquality
source ~/venvs/airquality/bin/activate
pip install --upgrade pip
pip install mh-z19
```

Re-activate the environment (`source ~/venvs/airquality/bin/activate`) before running the project or installing additional Python packages. Using `--system-site-packages` lets the virtual environment reuse Python packages installed via `apt` (e.g. `python3-numpy`) so they do not need to be rebuilt from source.

### VMA342
#### General
- The commands below assume the virtual environment from the MH-Z19 section is active.
- Install RPi.GPIO with `export CFLAGS=-fcommon` and `pip3 install RPi.GPIO` ([source](https://raspberrypi.stackexchange.com/questions/119632/ubuntu-20-10-and-gpio))
- Enable I2C via `sudo raspi-config` ([source](https://raspberrypi.stackexchange.com/questions/66145/raspberry-pi-3-not-detecting-i2c-device)): `Interface Options -> I2C -> Yes`
- Optionally adding the I2C module to the kernel: `sudo nano /etc/modules` and add `i2c-dev` to the end of the file.
- Reduce the baudrate in order to make the sensor compatible with Raspberry Pi: `sudo nano /boot/firmware/config.txt` and add `dtparam=i2c_arm_baudrate=10000` ([source](https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/i2c-clock-stretching)).

Tip: `i2cdetect -y 1` shows the current I2C connections.

#### BME280 (temperature + humidity + air pressure)

- Ensure the virtual environment is active, then run `pip install RPi.bme280`

Example to try out the CCS811 library:
```python
import smbus2
import bme280

port = 1
address = 0x77
bus = smbus2.SMBus(port)

calibration_params = bme280.load_calibration_params(bus, address)

# the sample method will take a single reading and return a
# compensated_reading object
data = bme280.sample(bus, address, calibration_params)

# the compensated_reading class has the following attributes
print(data.id)
print(data.timestamp)
print(data.temperature)
print(data.pressure)
print(data.humidity)

# there is a handy string representation too
print(data)
```

#### CCS811 (TVOC + eCO2)
*⚠️ STILL WIP. A BUG OCCURS WHEN READING OUT THE TVOC (and eCO2) VALUES*

[datasheet](https://cdn.sparkfun.com/assets/learn_tutorials/1/4/3/CCS811_Datasheet-DS000459.pdf)

- `pip3 install adafruit-circuitpython-ccs811`

Example to try out the [CCS811 library](https://pypi.org/project/adafruit-circuitpython-ccs811/):
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

### Streamlit
- `pip install streamlit==0.62.0` ([Installing Streamlit>0.62 on Raspberry Pi isn't straightforward because of dependency on PyArrow](https://discuss.streamlit.io/t/raspberry-pi-streamlit/2900/6))
- `pip install "click<8"` (Streamlit 0.62 expects Click 7.x; Debian's Click 8.x causes `AttributeError: module 'click' has no attribute 'get_os_args'`)
- Python 3.12+ requires Streamlit's WebSocket message size limit to be an integer. After installing Streamlit, edit `$HOME/venvs/airquality/lib/python3.13/site-packages/streamlit/server/server_util.py` and change `MESSAGE_SIZE_LIMIT = 50 * 1e6` to `MESSAGE_SIZE_LIMIT = int(50 * 1e6)` (or `50 * 1_000_000`).
- Bind Streamlit to all interfaces so other devices on your LAN can reach it: run `streamlit run src/dashboard.py --server.address 0.0.0.0 --server.port 8501` (or set the same values in `~/.streamlit/config.toml`).
- If you install Streamlit outside the virtual environment (e.g. with `pip install --user`), add your local bin directory to `PATH` so the `streamlit` command is found: `export PATH="$HOME/.local/bin:$PATH"` ([source](https://discuss.streamlit.io/t/command-not-found/741/7)). When using the virtual environment described above, the `streamlit` binary is already on `PATH` after `source ~/venvs/airquality/bin/activate`.

**Optional**

For some reason a `tornado.iostream.StreamClosedError: Stream is closed` error might occur after a running the Streamlit dashboard for a while. This can be resolved by editing the files inside your virtual environment (e.g. `~/venvs/airquality/lib/python3.13/site-packages/streamlit/server/…`):
- Change `MESSAGE_SIZE_LIMIT` in `server_util.py` from `50 * 1e6` to `600 * 1e6`.
- Change the `websocket_ping_timeout` parameter in `Server.py` from `60` to `200`.

### Automatically run the scripts on startup
- `chmod 664 ~/Documents/rpi-airquality/src/monitor.py`
-  Run `crontab -e` and append the following command to the bottom of the file:
```
@reboot (/bin/sleep 30; $HOME/venvs/airquality/bin/python $HOME/Documents/rpi-airquality/src/monitor.py > $HOME/cronjoblog-monitor 2>&1)
@reboot (/bin/sleep 30; $HOME/venvs/airquality/bin/streamlit run $HOME/Documents/rpi-airquality/src/dashboard.py --server.address 0.0.0.0 --server.port 4202 > $HOME/cronjoblog-dashboard 2>&1)
*/5 * * * * /bin/ping -c 2 www.google.com > $HOME/cronjoblog-ping.txt 2>&1
```
This will start the monitoring script and Streamlit dashboard on startup. Logs (including the optional keep-alive ping) will be printed to the specified files under your home folder.

Note that these commands call the interpreter and Streamlit executable directly from the virtual environment so no extra PATH changes are required. If your virtual environment lives elsewhere, update the paths accordingly. Cron runs with a minimal PATH (`/bin:/usr/bin`), so absolute paths (or environment variables such as `$HOME`) avoid command-not-found errors ([source](https://serverfault.com/questions/449651/why-is-my-crontab-not-working-and-how-can-i-troubleshoot-it)).

Extra: to confirm the cron jobs have run, use the system journal on recent Raspberry Pi OS releases: `sudo journalctl -u cron --since "10 minutes ago"`. On older setups that still log to `/var/log/syslog`, `grep CRON /var/log/syslog` remains an option. Tail the log files with `tail -F $HOME/cronjoblog-monitor` and `tail -F $HOME/cronjoblog-dashboard`.

### Turn of the LED's of the Raspberry Pi (optional)
**Raspberry Pi 3**
- Run `crontab -e` and append the following command to the bottom of the file ([source](https://forums.raspberrypi.com/viewtopic.php?t=149126#p1306079)):
```
@reboot (/bin/sleep 30; sudo sh -c 'echo 0 > /sys/class/leds/led0/brightness')
@reboot (/bin/sleep 30; sudo sh -c 'echo 0 > /sys/class/leds/led1/brightness')
```

**Raspberry Pi 4**
- `sudo nano /boot/config.txt`
- Add the following lines below the `[Pi4]` settings ([source](https://forums.raspberrypi.com/viewtopic.php?t=252049)):
```
# Disable the PWR LED
dtparam=pwr_led_trigger=none
dtparam=pwr_led_activelow=off
# Disable the Activity LED
dtparam=act_led_trigger=none
dtparam=act_led_activelow=off
# Disable ethernet port LEDs
dtparam=eth_led0=4
dtparam=eth_led1=4
```
- The lights will turn off once the Raspberry Pi has been restarted.

## View dashboard
The Streamlit dashboard can be viewed from any device on the same network by visiting http://raspberrypi.local:4202/ (or http://<pi-ip>:4202/ if mDNS isn’t available).