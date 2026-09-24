# Submission Notes

This services is a python script to log sensor data to the systemd journal. I used an Ubuntu 24.04 WSL instance to develop this.

## Installation
- sensor_service.py is installed to /opt/sensor_service. 
- sensor.service should be installed in /etc/systemd/system
- a venv should be setup inside /opt/sensor_service utilizing requirements.txt

to run, open a terminal and run pty_setup.sh to start the mock sensor and create the pty. Then manually activate the venv in the install directory (`source .venv/bin/activate`) and run python `sensor_service.py /tmp/ttyMOCK_PEER`

## Implementation Notes

I utilized AI as a knowledge source, and I had it write an implementation of the CRC algorithm used. The way I handle the buffer is less than ideal. I wrote this code around the idea that at the beginning of the search for a frame, I would check and drain the serial buffer into the working buffer. The problem with this is that this service NEVER blocks, which is pretty wasteful of resources. 

I avoided looking at the implementation of mock.py since in my experience, the exact behavior of a real piece of hardware is never as explicitly stated as this mock code. Instead, I relied solely on the Sensor Specification section in the readme to develop this code. 

That being said, in real world scenarios, mock objects in code are quite common. If I was handed a mock object to simulate a real sensor, I would absolutely use that to my advantage. 

There is at least one bug in how the CRC is calculated or evaluated against the CRCs in the frame.

Also, the instructions specified that I didn't have to install this in my environment, and since the environment is generically specified as any linux, I can't guarantee that the systemd service file will work as intended if you do try to install it. 
