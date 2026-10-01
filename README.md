# Submission Notes

This services is a python script to log sensor data to the systemd journal. I used an Ubuntu 24.04 WSL instance to develop this.

## Installation
- sensor_service.py is installed to /opt/sensor_service. 
- sensor.service should be installed in /etc/systemd/system
- a venv should be setup inside /opt/sensor_service utilizing requirements.txt

## Running

to run, open a terminal and run `pty_setup.sh` to start the mock sensor and create the pty pair. Then, run `setup_env.sh` to create a python virtual env, install the packages in requirements.txt, and activate it. Finally,  run `python3 sensor_service.py /tmp/ttyMOCK_PEER`. Outputs should print to the terminal. 

## Implementation Notes

I utilized AI as a knowledge source, and I had it write an implementation of the CRC algorithm used. The way I handle the buffer is less than ideal. I wrote this code around the idea that at the beginning of the search for a frame, I would check and drain the serial buffer into the working buffer. The problem with this is that this service NEVER blocks, which is pretty wasteful of resources. 

I avoided looking at the implementation of mock.py since in my experience, the exact behavior of a real piece of hardware is never as explicitly stated as this mock code. Instead, I relied solely on the Sensor Specification section in the readme to develop this code. 

That being said, in real world scenarios, mock objects in code are quite common. If I was handed a mock object to simulate a real sensor, I would absolutely use that to my advantage. 

There is at least one bug in how the CRC is calculated or evaluated against the CRCs in the frame.

Also, the instructions specified that I didn't have to install this in my environment, and since the environment is generically specified as any linux, I can't guarantee that the systemd service file will work as intended if you do try to install it. 

## Notes from examination after submission
After submission, I found the CRC bug was caused by bad arguments to `int.from_bytes()` without specifying `byteorder='little'`, the method assumes big endian ordering. After fixing this, the script works. I also found the running and installation instructions and scripts to be awkward, so I have added a few lines and fixed a few bugs (replaced $! with #!) to make setup and running much smoother. The data points the service reports are also values formatted in a way that is not intuitive for a human reader, and after looking at `mock.py`, I can see that is because these are integers that represent 10ths of a unit. I also noticed that the mock sensor occasionally fudges the data frame to simulate corrupted frames, so the CRC check that I assumed to be a formality was actually in play in this scenario. 
### Areas for improvement
- **Unit Tests** There are not unit tests for this code. This would probably require some refactoring to facilitate this. Right now, the whole service is just two functions. It would be easier to unit test the results if the parsed data ended up in dataclasses rather than parsing plain-text outputs. 
- **Socket Buffer** The service code spins forever in the main loop. This is not ideal because its a non-blocking call when the port is initialized with `timeout=None`. This doesn't hurt anything in regards to the service, but its pretty wasteful especially since the sensor only publishes data every 2 seconds. Ideally, a `sleep()` or a blocking read of the socket buffer would preserve cpu for other processess that may be running.
- **Structs** If the protocol became much more complicated, I would add the python struct library to help facilitate unpacking of dataframes.
- **Error Handling** There is minimal error handling. There is no report of corrupted, or missing frames. What happens if the sensor doesn't initialize properly or isn't there? Does the file descriptor for the pty exist or not? What if data is never published over the pty? These error cases need to be enumerated and accounted for. 
- **Timestamps** I noticed there is not time data in the observations. Realistically, I would expect a need to correlate an observation with the time it was observed. This really wasn't specified in the problem statement, so I didn't design for it, but this is almost certainly something that the consumer of this data would want. 
- **Type Hints** I did not bother with type hints for this small service, but in practice, I am a huge fan of explicit typing. 