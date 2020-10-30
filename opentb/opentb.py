#! /usr/bin/env python3

"""
Helper script to flash a hex to a set of OpenWSN OpenTestbed motes.

usage: opentb-cli [-h] [--board BOARD] [--devices DEVICES [DEVICES ...]]
                  [--hexfile HEXFILE]
                  [--loglevel {debug,info,warning,error,fatal,critical}]
                  {discover,echo,program}

positional arguments:
  {discover,echo,program}
                        Supported MQTT commands

optional arguments:
  -h, --help            show this help message and exit
  --board BOARD, --b BOARD
                        Board name (Only openmote-b is currently supported)
  --devices DEVICES [DEVICES ...], --d DEVICES [DEVICES ...]
                        Mote address or otbox id, 'all' for all motes/boxes)
  --hexfile HEXFILE, --x HEXFILE
                        Hexfile program to bootload
  --loglevel {debug,info,warning,error,fatal,critical}
                        Python logger log level

example:
- discover motes 'echo':
    opentb-cli echo --d otbox02 otbox10

- discover motes 'discover':
    python opentb.py discover --d otbox02

- program motes 'program':
    python opentb.py program --b openmote-b --d 00-12-4b-00-14-b5-b5-45 00-12-4b-00-14-b5-b5-e4 --x example/main.ihex
    python opentb.py program --b openmote-b --d all --x  example/main.ihex
"""

import abc
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

COMMANDS_BOXES = ('discover', 'echo')
COMMANDS_MOTES = ('program', )
COMMANDS = COMMANDS_BOXES + COMMANDS_MOTES

LOG_HANDLER = logging.StreamHandler()
LOG_HANDLER.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
LOG_LEVELS = ('debug', 'info', 'warning', 'error', 'fatal', 'critical')
LOGGER = logging.getLogger("opentb")

USAGE_EXAMPLE = '''example:
- discover motes 'echo':
    opentb-cli echo --d otbox02 otbox10

- discover motes 'discover':
    python opentb.py discover --d otbox02

- program motes 'program':
    python opentb.py program --b openmote-b --d 00-12-4b-00-14-b5-b5-45 00-12-4b-00-14-b5-b5-e4 --x example/main.ihex
    python opentb.py program --b openmote-b --d all --x  example/main.ihex
'''

PARSER = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter, epilog=USAGE_EXAMPLE)
PARSER.add_argument('cmd', choices=COMMANDS, default='program',
                    help='Supported MQTT commands')
PARSER.add_argument( '--board', '--b', default='openmote-b',
                    help='Board name (Only openmote-b is currently supported)')
PARSER.add_argument('--devices', '--d', nargs='+', default='all',
                    help='Mote address or otbox id, \'all\' for all motes/boxes)')
PARSER.add_argument('--hexfile', '--x', default=None,
                     help='Hexfile program to bootload')
PARSER.add_argument('--loglevel', choices=LOG_LEVELS, default='info',
                    help='Python logger log level')

OPENMOTE_B_FLASHSIZE = 512*1024
CC2538_FLASHPAGE_SIZE = 2048


class OpenTBCmdRunner(object):

    CLIENT_ID = 'OpenWSN'
    BASE_MOTE_TOPIC = 'opentestbed/deviceType/mote/deviceId'
    BASE_BOX_TOPIC = 'opentestbed/deviceType/box/deviceId'
    # in seconds, should be larger than the time starting from publishing
    # message until receiving the response
    RESPONSE_TIMEOUT = 60

    def __init__(self, devices):
        # initialize parameters
        self.devices = devices

        # create queue for receiving resp messages
        self._queue = queue.Queue()

        # connect to MQTT
        self._connected = False
        self._client = mqtt.Client(self.CLIENT_ID)
        self._client.on_connect = self._on_mqtt_connect
        self._client.on_message = self._on_mqtt_message
        self._client.connect(BROKER_ADDRESS)
        self._client.loop_start()

        # wait for connection
        while not self._connected:
            pass

        # publish to devices
        if self.devices == 'all':
            if self.base_topic == self.BASE_MOTE_TOPIC:
                num_responses = NUMBER_OF_MOTES
            else:
                num_responses = NUMBER_OF_BOXES
            self._publish('all', self._gen_payload())
        else:
            num_responses = len(self.devices)
            for dev in self.devices:
                self._publish(dev, self._gen_payload())

        # wait maximum RESPONSE_TIMEOUT seconds before return
        LOGGER.debug("Waiting for {} responses".format(num_responses))
        timedout = False
        for _ in range(0, num_responses):
            try:
                if timedout:
                    timeout = 0
                else:
                    timeout = self.RESPONSE_TIMEOUT
                self._queue.get(timeout=timeout)
            except queue.Empty as error:
                timedout = True
                LOGGER.error("Response message timeout in {} seconds".format(
                    self.RESPONSE_TIMEOUT))

        # cleanup
        self._finish()
        self._client.loop_stop()

    @abc.abstractmethod
    def _gen_payload(self):
        raise NotImplementedError("Should be implemented by child class")

    @abc.abstractmethod
    def _parse_response(self):
        raise NotImplementedError("Should be implemented by child class")

    @abc.abstractmethod
    def _finish(self):
        raise NotImplementedError("Should be implemented by child class")

    def _dev_from_topic(self, topic):
        pat = re.compile('{}/(.+)/resp/{}'.format(self.base_topic, self.cmd))
        return pat.match(topic).group(1)

    def _gen_rep_topic(self, dev, base_topic):
        return '{}/{}/resp/{}'.format(base_topic, dev, self.cmd)

    def _subscribe(self, client, devices):
        LOGGER.debug("Subcribing to topics:")
        if devices == 'all':
            topic = '{}/{}/resp/{}'.format(self.base_topic, '+', self.cmd)
            LOGGER.debug("    {}".format(topic))
            client.subscribe(topic)
        else:
            topics = [self._gen_rep_topic(dev, self.base_topic) for dev in devices]
            for topic in topics:
                LOGGER.debug("    {}".format(topic))
                client.subscribe(topic)

    def _publish(self, dev, payload):
        topic = '{}/{}/cmd/{}'.format(self.base_topic, dev, self.cmd)
        LOGGER.debug("Publishing to topic:")
        LOGGER.debug("    {}".format(topic))
        self._client.publish(topic=topic, payload=json.dumps(payload))

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        LOGGER.info("Connected to broker {}".format(BROKER_ADDRESS))
        self._connected = True
        self._subscribe(client, self.devices)

    def _on_mqtt_message(self, client, userdata, message):
        self._parse_response(message)


class CmdEcho(OpenTBCmdRunner):

    def __init__(self, boxes):
        self.cmd = 'echo'
        self.base_topic = self.BASE_BOX_TOPIC
        self.responses = []
        # initialize the parent class
        OpenTBCmdRunner.__init__(self, devices=boxes)

    def _gen_payload(self):
        return {
            'token': 123,
            'payload': 'Echo Test String'
        }

    def _finish(self):
        LOGGER.info("-------------------------------------------------")
        LOGGER.info("{} motes responded".format(len(self.responses)))
        for response in self.responses:
            LOGGER.info("   {}".format(response))
        LOGGER.info("-------------------------------------------------")

    def _parse_response(self, message):
        payload_json = json.loads(message.payload)
        box = self._dev_from_topic(message.topic)
        LOGGER.debug("{}: responded {}".format(message.topic, payload_json))

        if payload_json['success']:
            self.responses.append("{}: {}".format(box,
                payload_json['returnVal']['payload']))
        else:
            LOGGER.error("'status' on box {} failed".format(box))
        if self.devices == ['all']:
            if len(self.discovered) == NUMBER_OF_BOXES:
                self._queue.put('unblock')
        else:
            self._queue.put('unblock')


class CmdProgram(OpenTBCmdRunner):

    def __init__(self, motes, hexfile):
        self.base_topic = self.BASE_MOTE_TOPIC
        self.cmd = 'program'

        # check image
        assert self._check_image(hexfile)
        self.image_name = ''
        with open(hexfile,'rb') as f:
            self.image = base64.b64encode(f.read())
        if os.name=='nt':       # Windows
            self.image_name = hexfile.split('\\')[-1]
        elif os.name=='posix':  # Linux
            self.image_name = hexfile.split('/')[-1]
        # initialize statistic result
        self.response = {
            'success_count':   0 ,
            'msg_count':   0 ,
            'failed_msg_topic': [],
            'success_msg_topic': []
        }
        # initialize the parent class
        OpenTBCmdRunner.__init__(self, devices=motes)


    def _check_image(self, image):
        '''
        Check bootload backdoor is configured correctly
        '''
        bootloader_backdoor_enabled   = False
        extended_linear_address_found = False

        # When building RIOT with OpenWSN-fw + SUIT the Customer
        # Configuration Area (CCA) is not touched. The Customer
        # CCA holds the Bootloader Backdoor Configuration,
        # Application Entry Point, flashpage lock bits.
        # When using SUIT + cc2538 RIOT does not touch this region so
        # that the entry point is not changed when updating the device
        # with new firmware (the entry point must allways be riot's
        # bootloader).
        # The CCA field resides in the last flashpage, for cc2538
        # each flashpage is 2048 bytes. Only openmote-b are present
        # in the testbed and the flashsize is allways 512Kb. Since
        # flashing at an offset is not supported only check that the
        # target firmware does not override the CCA region.
        if '.bin' in image:
            if os.path.getsize(image) < (OPENMOTE_B_FLASHSIZE) - CC2538_FLASHPAGE_SIZE:
                bootloader_backdoor_enabled = OpenTBCmdRunner
                return bootloader_backdoor_enabled

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

    def _gen_payload(self):
        return {
            'token': 123,
            'description': self.image_name,
            'hex': self.image.decode('utf-8'),
        }

    def _parse_response(self, message):
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
                self._queue.put('unblock')
        else:
            self._queue.put('unblock')


    def _finish(self):
        motes = []
        pattern = re.compile('{}/(.+)/resp/program'.format(self.base_topic))
        LOGGER.info("-------------------------------------------------")
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
        LOGGER.info("-------------------------------------------------")


class CmdDiscover(OpenTBCmdRunner):

    def __init__(self, boxes):
        self.cmd = 'discovermotes'
        self.discovered = []
        self.base_topic = self.BASE_BOX_TOPIC
        # initialize the parent class
        OpenTBCmdRunner.__init__(self, devices=boxes)

    def _gen_payload(self):
        return { 'token': 123 }

    def _finish(self):
        LOGGER.info("-------------------------------------------------")
        LOGGER.info("Discovered {} motes".format(len(self.discovered)))
        for mote in self.discovered:
            LOGGER.info("    {} {} {} {}".format(
                mote['box'], mote['eui64'], mote['port'], mote['status']))
        LOGGER.info("-------------------------------------------------")

    def _parse_response(self, message):
        payload_json = json.loads(message.payload)
        box = self._dev_from_topic(message.topic)
        LOGGER.debug("{}: responded {}".format(message.topic, payload_json))

        if payload_json['success']:
            for mote in payload_json['returnVal']['motes']:
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
            LOGGER.error("discover motes on box {} failed".format(box))
        if self.devices == ['all']:
            if len(self.discovered) == NUMBER_OF_BOXES:
                self._queue.put('unblock')
        else:
            self._queue.put('unblock')

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
    if cmd == 'program':
        if hexfile is None:
            LOGGER.critical("Provide a hex file with --hexfile")
            sys.exit(-1)
        CmdProgram(motes=devices, hexfile=hexfile)
    elif cmd == 'echo':
        CmdEcho(boxes=devices)
    elif cmd == 'discover':
        CmdDiscover(boxes=devices)


if __name__ == "__main__":
    main()
