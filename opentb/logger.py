#! /usr/bin/env python3

"""
Helper script to log opentestbed mqtt data to file.

usage: python logger.py [-h] [--broker BROKER] [--data-topic DATA_TOPIC] [--name NAME]
                        [--loglevel {debug,info,warning,error,fatal,critical}] [--runtime RUNTIME] [--timestamp TIMESTAMP]
                        [directory]

positional arguments:
  directory             Logs directory

optional arguments:
  -h, --help            show this help message and exit
  --broker BROKER, --b BROKER
                        MQTT broker address
  --data-topic DATA_TOPIC
                        Default topic to subscribe for data
  --name NAME, --lf NAME
                        Log file base name
  --loglevel {debug,info,warning,error,fatal,critical}
                        Python logger log level
  --runtime RUNTIME, --e RUNTIME
                        Logging Time in seconds, 0 means until interrupted
  --timestamp TIMESTAMP, --t TIMESTAMP
                        Timestamp to append to log file name, if not provided creation time is used

example:

    python logger.py testlogs
    python logger.py testlogs --runtime 60 --name dummyname
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

LOGFILE_NAME = 'opentestbed'
DEFAULT_BROKER = 'argus.paris.inria.fr'
UDP_INJECT_TOPIC = 'opentestbed/uinject/arrived'

LOG_HANDLER = logging.StreamHandler()
LOG_HANDLER.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
LOG_LEVELS = ('debug', 'info', 'warning', 'error', 'fatal', 'critical')

USAGE_EXAMPLE = '''example:

    python logger.py testlogs
    python logger.py testlogs --runtime 60 --name dummyname
'''

PARSER = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter, epilog=USAGE_EXAMPLE)
PARSER.add_argument('directory', nargs='?', default='logs',
                    help='Logs directory')
PARSER.add_argument('--broker', '--b', default=DEFAULT_BROKER,
                    help='MQTT broker address')
PARSER.add_argument('--data-topic', default=UDP_INJECT_TOPIC,
                    help='Default topic to subscribe for data')
PARSER.add_argument( '--name', '--lf', default=LOGFILE_NAME ,
                    help='Log file base name')
PARSER.add_argument('--loglevel', choices=LOG_LEVELS, default='info',
                    help='Python logger log level')
PARSER.add_argument( '--runtime', '--e', type=float, default=0,
                    help='Logging Time in seconds, 0 means until interrupted')
PARSER.add_argument( '--timestamp', '--t', type=float, default=None,
                    help='Timestamp to append to log file name, '
                    'if not provided creation time is used')


class MqttDataLogger(object):

    def __init__(self, broker, topic, outfile):

        self.broker = broker
        self.topic = topic
        self.outfile = outfile

        # Connect to broker and start loop
        self.client = mqttClient.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.broker)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        log = logging.getLogger("opentb-logger")
        if rc:
            log.error("Connection failed")
        else:
            log.info("Connection succeeded")
            client.subscribe(self.topic)
            log.info("Subscribed to {}".format(self.topic))

    def _on_message(self, client, userdata, message):
        log = logging.getLogger("opentb-logger")
        log.info("Message received: {}".format(message.payload))
        _log_data(message.payload, self.outfile)


def _log_data(data, file_path):
    with open(file_path, 'a') as f:
        timestamp = datetime.datetime.now()
        log = {
            'timestamp': timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
            'data':json.loads(data)
        }
        f.write('{}\n'.format(json.dumps(log)))


def _create_directory(directory, clean=False, mode=0o755):
    """Directory creation helper with `clean` option.

    :param clean: tries deleting the directory before re-creating it
    """
    if clean:
        try:
            shutil.rmtree(directory)
        except OSError:
            pass
    os.makedirs(directory, mode=mode, exist_ok=True)


def _create_logfile(directory, name, timestamp=None):
    if timestamp is None:
        timestamp = int(time.time())
    log_file_name = '{}_{}.jsonl'.format(name, timestamp)

    file_path = os.path.join(directory, log_file_name)
    if os.path.exists(file_path):
        sys.exit("Log file {} already exists".format(log_file_name))
    else:
        try:
            open(file_path, 'w').close()
        except OSError as err:
            sys.exit('Failed to create a log file: {}'.format(err))
    return file_path


def _keep_running(start_time, run_time):
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

    # parse arguments
    subscribe_topic = args.data_topic
    directory = args.directory
    filename = args.name
    broker = args.broker
    runtime = args.runtime
    timestamp = args.timestamp

    # Create directory if needed
    _create_directory(directory)

    # Setup log file with date
    logfile = _create_logfile(directory, filename, timestamp)

    # Connect to broker and start loop
    mqtt_logger = MqttDataLogger(broker, subscribe_topic, logfile)

    # Run while not interrupted or while runtime has not elapsed
    start_time = time.time()
    try:
        while True and _keep_running(start_time, runtime):
            time.sleep(0.1)
    except KeyboardInterrupt:
        log.info("Keyboard Interrupt, forced exit!")
    finally:
        mqtt_logger.client.disconnect()
        mqtt_logger.client.loop_stop()


if __name__ == "__main__":
    main()
