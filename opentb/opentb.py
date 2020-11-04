#! /usr/bin/env python3

"""
Helper script to flash a hex to a set of OpenWSN OpenTestbed motes.

usage: opentb-cli [-h] [--board BOARD] [--devices DEVICES [DEVICES ...]] [--loglevel {debug,info,warning,error,fatal,critical}]
                  [--cmd {program,discovermotes}]
                  hexfile

positional arguments:
  hexfile               Hexfile program to bootload

optional arguments:
  -h, --help            show this help message and exit
  --board BOARD, --b BOARD
                        Board name (Only openmote-b is currently supported)
  --devices DEVICES [DEVICES ...], --d DEVICES [DEVICES ...]
                        Mote address or otbox id, use "all" indicating all motes/box)
  --loglevel {debug,info,warning,error,fatal,critical}
                        Python logger log level
  --cmd {program,discovermotes}, --c {program,discovermotes}
                        Supported MQTT commands

example:

- discover motes 'discovermotes':
    python opentb.py example/main.ihex --d otbox15 --cmd discovermotes

- program motes 'program':
    python opentb.py --b openmote-b --d 00-12-4b-00-14-b5-b4-98 example/main.ihex
    python opentb.py --b openmote-b --d all example/main.ihex
"""

import argparse
import base64
import json
import logging
import os
import paho.mqtt.client as mqtt
import queue
import random
import re
import sys
import time

BROKER_ADDRESS = "argus.paris.inria.fr"

NUMBER_OF_MOTES = 80
NUMBER_OF_BOXES = 14

COMMANDS = ('program', 'discovermotes')

LOG_HANDLER = logging.StreamHandler()
LOG_HANDLER.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
LOG_LEVELS = ('debug', 'info', 'warning', 'error', 'fatal', 'critical')
LOGGER = logging.getLogger("opentb")

USAGE_EXAMPLE = '''example:

- discover motes 'discovermotes':
    python opentb.py example/main.ihex --d otbox15 --cmd discovermotes

- program motes 'program':
    python opentb.py --b openmote-b --d 00-12-4b-00-14-b5-b4-98 example/main.ihex
    python opentb.py --b openmote-b --d all example/main.ihex
'''

PARSER = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter, epilog=USAGE_EXAMPLE)
PARSER.add_argument('hexfile', help='Hexfile program to bootload')
PARSER.add_argument( '--board', '--b', default='openmote-b',
                    help='Board name (Only openmote-b is currently supported)')
PARSER.add_argument('--devices', '--d', nargs='+', default='all',
                    help='Mote address or otbox id, use "all" indicating all motes/box)')
PARSER.add_argument('--loglevel', choices=LOG_LEVELS, default='info',
                    help='Python logger log level')
PARSER.add_argument('--cmd', '--c', choices=COMMANDS, default='program',
                    help='Supported MQTT commands')


class command_runner(object):

    CLIENT_ID = 'OpenWSN'
    BASE_MOTE_TOPIC = 'opentestbed/deviceType/mote/deviceId'
    BASE_BOX_TOPIC = 'opentestbed/deviceType/box/deviceId'
    # in seconds, should be larger than the time starting from publishing
    # message until receiving the response
    RESPONSE_TIMEOUT = 60

    def __init__(self, devices, hexfile, cmd):
        # initialize parameters
        self.devices = devices
        self.image = None
        self.cmd = cmd
        self.responses = 0

        if self.cmd == 'program':
            # check image
            assert self._check_image(hexfile)
            self.image_name = ''
            with open(hexfile,'rb') as f:
                self.image = base64.b64encode(f.read())
            if os.name=='nt':       # Windows
                self.image_name = hexfile.split('\\')[-1]
            elif os.name=='posix':  # Linux
                self.image_name = hexfile.split('/')[-1]

            self.base_topic = self.BASE_MOTE_TOPIC

            # initialize statistic result
            self.response = {
                'success_count':   0 ,
                'msg_count':   0 ,
                'failed_msg_topic': [],
                'success_msg_topic': []
            }
            device_num = NUMBER_OF_MOTES
        elif self.cmd == 'discovermotes':
            self.discovered = []
            self.base_topic = self.BASE_BOX_TOPIC
            device_num = NUMBER_OF_BOXES

        # connect to MQTT
        self.connected                 = False
        self.mqttclient                = mqtt.Client(self.CLIENT_ID)
        self.mqttclient.on_connect     = self._on_mqtt_connect
        self.mqttclient.on_message     = self._on_mqtt_message
        self.mqttclient.connect(BROKER_ADDRESS)
        self.mqttclient.loop_start()

        while not self.connected:
            pass

        # create queue for receiving resp messages
        self.cmd_response_success_queue = queue.Queue()

        # publish to devices
        if self.devices == 'all':
            self._publish_to_device('all')
        else:
            device_num = len(self.devices)
            for dev in self.devices:
                self._publish_to_device(dev)

        # wait maximum RESPONSE_TIMEOUT seconds before return
        LOGGER.debug("Waiting for {} responses".format(device_num))
        timedout = False
        for _ in range(0, device_num):
            try:
                if timedout:
                    timeout = 0
                else:
                    timeout = self.RESPONSE_TIMEOUT
                self.cmd_response_success_queue.get(timeout=timeout)
            except queue.Empty as error:
                timedout = True
                LOGGER.error("Response message timeout in {} seconds".format(
                    self.RESPONSE_TIMEOUT))

        # print results
        self._print_results()
        # cleanup
        self.mqttclient.loop_stop()


    def _publish_to_device(self, dev):
        if self.cmd == 'program':
            payload = {
                'token': 123,
                'description': self.image_name,
                'hex': self.image.decode('utf-8'),
            }
        if self.cmd == 'discovermotes':
            payload = {'token': 123 }
        topic = '{}/{}/cmd/{}'.format(self.base_topic, dev, self.cmd)
        LOGGER.debug("Publish to topic {}".format(topic))
        self.mqttclient.publish(topic=topic, payload=json.dumps(payload))


    def _check_image(self, image):
        '''
        Check bootload backdoor is configured correctly
        '''
        bootloader_backdoor_enabled   = False
        extended_linear_address_found = False

        with open(image,'r') as f:
            for line in f:

                # looking for data at address 0027FFD4
                # refer to: https://en.wikipedia.org/wiki/Intel_HEX#Record_types

                # looking for upper 16bit address 0027
                if line[:15] == ':020000040027D3':
                    extended_linear_address_found = True

                # check the lower 16bit address FFD4

                # | 1:3 byte count | 3:7 address | 9:17 32-bit field of the lock bit page (the last byte is backdoor configuration) |
                # 'F6' = 111        1                               0           110
                #        reserved   backdoor and bootloader enable  active low  PA pin used for backdoor enabling (PA6)
                if extended_linear_address_found and line[3:7] == 'FFD4' and int(line[1:3], 16)>4  and line[9:17] == 'FFFFFFF6':
                    bootloader_backdoor_enabled = True

        return bootloader_backdoor_enabled


    def _mote_resp_topic(self, mote, base_topic):
        return '{}/{}/resp/{}'.format(base_topic, mote, self.cmd)


    def _subscribe(self, client, deviceIds):
        if deviceIds == 'all':
            topic = '{}/{}/resp/{}'.format(
                self.base_topic, '+', self.cmd)
            LOGGER.debug("subscribing to {}".format(topic))
            client.subscribe(topic)
        else:
            topics = [self._mote_resp_topic(dev, self.base_topic,) for dev in deviceIds]
            for topic in topics:
                LOGGER.debug("subcribing to topics: {}".format(topic))
                client.subscribe(topic)


    def _on_mqtt_connect(self, client, userdata, flags, rc):
        LOGGER.info("connected to broker {}".format(BROKER_ADDRESS))
        self.connected = True
        self._subscribe(client, self.devices)


    def _parse_discover_response(self, message):
        '''
        Parse and record discovered motes
        '''
        p_json = json.loads(message.payload)

        pattern = re.compile('{}/(.+)/resp/discovermotes'.format(self.base_topic))
        box = pattern.match(message.topic).group(1)
        LOGGER.debug("{}: responded {}".format(message.topic,
            json.loads(message.payload)))

        if p_json['success']:
            for mote in p_json['returnVal']['motes']:
                if 'EUI64' in mote:
                    eui64 = mote['EUI64']
                else :
                    eui64 = None
                mote_json = {
                    'box': box,
                    'port': mote['serialport'],
                    'eui64': eui64,
                    'status': 1 if mote['bootload_success'] else 0,
                }
                self.discovered.append(mote_json)
        else:
            lof.error("discover motes on box {} failed".format(box))
        if self.devices == ['all']:
            if self.cmd == 'program':
                if len(self.discovered) == NUMBER_OF_MOTES:
                    self.cmd_response_success_queue.put('unblock')
            elif self.cmd == 'discovermotes':
                if len(self.discovered) == NUMBER_OF_BOXES:
                    self.cmd_response_success_queue.put('unblock')
        else:
            self.cmd_response_success_queue.put('unblock')


    def _parse_program_response(self, message):
        '''
        Parse and record number of message received and success status
        '''
        if 'exception' in json.loads(message.payload):
            LOGGER.debug("{}: exception ignored".format(message.topic))
            return
        else:
            LOGGER.debug("{}: responded {}".format(message.topic,
                json.loads(message.payload)))
            self.response['msg_count'] += 1

        if json.loads(message.payload)['success']:
            self.response['success_count'] += 1
            self.response['success_msg_topic'].append(message.topic)
        else:
            self.response['failed_msg_topic'].append(message.topic)

        if self.devices == ['all']:
            if self.response['msg_count'] == NUMBER_OF_MOTES:
                self.cmd_response_success_queue.put('unblock')
        else:
            self.cmd_response_success_queue.put('unblock')


    def _on_mqtt_message(self, client, userdata, message):
        '''
        Parse responses
        '''
        if self.cmd == 'program':
            self._parse_program_response(message)
        elif self.cmd == 'discovermotes':
            self._parse_discover_response(message)


    def _print_results(self):
        motes = []
        if self.cmd == 'program':
            pattern = re.compile('{}/(.+)/resp/program'.format(self.BASE_MOTE_TOPIC))

            LOGGER.info("----------------------------------------------------")
            LOGGER.info("{} of {} motes reported with success".format(
                self.response['success_count'],
                self.response['msg_count']
            ))
            for topic in self.response['success_msg_topic']:
                mote = pattern.match(topic).group(1)
                motes.append(mote)
                LOGGER.info("    {} OK".format(mote))
            if self.response['msg_count'] > self.response['success_count']:
                for topic in self.response['failed_msg_topic']:
                    mote = pattern.match(topic).group(1)
                    motes.append(mote)
                    LOGGER.info("    {} FAIL".format(mote))
            if len(motes) != len(self.devices):
                for device in self.devices:
                    if device not in motes:
                        LOGGER.info("    {} MUTE".format(device))

            LOGGER.info("----------------------------------------------------")
        elif self.cmd == 'discovermotes':
            LOGGER.info("----------------------------------------------------")
            LOGGER.info("Discovered {} motes".format(len(self.discovered)))
            for mote in self.discovered:
                LOGGER.info("    {} {} {} {}".format(
                    mote['eui64'], mote['box'], mote['port'], mote['status']))
            LOGGER.info("----------------------------------------------------")


def main(args=None):
    args = PARSER.parse_args()

    # setup logger
    if args.loglevel:
        loglevel = logging.getLevelName(args.loglevel.upper())
        LOGGER.setLevel(loglevel)
    LOGGER.addHandler(LOG_HANDLER)
    LOGGER.propagate = False

    # parse args
    devices = args.devices
    hexfile = args.hexfile
    cmd = args.cmd

    if len(devices) != len(args.devices):
        duplicates = len(args.devices) - len(devices)
        LOGGER.error('{} duplicates removed'.format(duplicates))


    # run command on devices list
    command_runner(devices=devices, cmd=cmd, hexfile = hexfile)


if __name__ == "__main__":
    main()
