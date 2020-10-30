#! /usr/bin/env python3

"""
Helper script to log opentestbed mqtt data

usage: logger.py [-h] [--broker BROKER] [--logfile LOGFILE]
                 [--loglevel {debug,info,warning,error,fatal,critical}] [--runtime RUNTIME]
                 [log_directory]

positional arguments:
  log_directory         Logs directory

optional arguments:
  -h, --help            show this help message and exit
  --broker BROKER, --b BROKER
                        MQTT broker address
  --logfile LOGFILE, --lf LOGFILE
                        Log file base name
  --loglevel {debug,info,warning,error,fatal,critical}
                        Python logger log level
  --runtime RUNTIME, --t RUNTIME
                        Logging Time in seconds, 0 means until interrupted

example:

    ....
"""

import argparse
import datetime
import json
import logging
import os
import paho.mqtt.client as mqttClient
import shutil
import sys
import time

EXPERIMENT_NAME = 'udp_inject'
LOGFILE_NAME = 'udp_inject'
DEFAULT_BROKER = 'argus.paris.inria.fr'
UDP_INJECT_TOPIC = 'opentestbed/uinject/arrived'

LOG_HANDLER = logging.StreamHandler()
LOG_HANDLER.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
LOG_LEVELS = ('debug', 'info', 'warning', 'error', 'fatal', 'critical')

USAGE_EXAMPLE = '''example:

    ....
'''

PARSER = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter, epilog=USAGE_EXAMPLE)
PARSER.add_argument('log_directory', nargs='?', default='logs',
                    help='Logs directory')
PARSER.add_argument('--broker', '--b', default=DEFAULT_BROKER,
                    help='MQTT broker address')
PARSER.add_argument( '--logfile', '--lf', default=LOGFILE_NAME ,
                    help='Log file base name')
PARSER.add_argument('--loglevel', choices=LOG_LEVELS, default='info',
                    help='Python logger log level')
PARSER.add_argument( '--runtime', '--t', type=float, default=0,
                    help='Logging Time in seconds, 0 means until interrupted')


filepath = ''


def _on_connect(client, userdata, flags, rc):
    log = logging.getLogger("opentb-logger")
    if rc:
        log.error("Connection failed")
    else:
        log.info("Connection succeeded")
        client.subscribe(UDP_INJECT_TOPIC)
        log.info("Subscribed to {}".format(UDP_INJECT_TOPIC))


def _on_message(client, userdata, message):
    log = logging.getLogger("opentb-logger")
    log.info("Message received: {}".format(message.payload))
    _log_data(message.payload)


def _log_data(data):
    with open(filepath, 'a') as f:
        log = {
            'name': EXPERIMENT_NAME,
            'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            'data':json.loads(data)
        }
        f.write('{}\n'.format(json.dumps(log)))


def _create_log_directory(directory, clean=False, mode=0o755):
    """Directory creation helper with `clean` option.

    :param clean: tries deleting the directory before re-creating it
    """
    if clean:
        try:
            shutil.rmtree(directory)
        except OSError:
            pass
    os.makedirs(directory, mode=mode, exist_ok=True)


def _create_log_file(directory, filename):
    _create_log_directory(directory)
    # decide a log file name and create it
    timestamp = int(time.time())
    log_file_name = '{}-{}.jsonl'.format(filename, timestamp)

    file_path = os.path.join(directory, log_file_name)
    if os.path.exists(file_path):
        sys.exit("LogFile already exists")
    else:
        try:
            open(file_path, 'w').close()
        except OSError as err:
            sys.exit('Failed to create a log file: {}'.format(err))
    global filepath
    filepath = file_path


def _should_run(start_time, run_time):
    if run_time == 0:
        return True
    elif start_time + run_time > time.time():
        return True
    else:
        return False


def main(args=None):
    args = PARSER.parse_args()

    # Setup logger
    log = logging.getLogger("opentb-logger")
    if args.loglevel:
        loglevel = logging.getLevelName(args.loglevel.upper())
        log.setLevel(loglevel)

    log.addHandler(LOG_HANDLER)
    log.propagate = False

    # Setup log file with date
    _create_log_file(args.log_directory, args.logfile)

    # Connect to broker and start loop
    client = mqttClient.Client("Python")
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(args.broker)
    client.loop_start()

    start_time = time.time()
    try:
        # Run while not interrupted
        while True and _should_run(start_time, args.runtime):
            time.sleep(0.1)
    except KeyboardInterrupt:
        log.info("Keyboard Interrupt, forced exit!")
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
