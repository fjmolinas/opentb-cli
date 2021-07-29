#! /usr/bin/env python3

import argparse
import datetime
import json
import logging
import os
import paho.mqtt.client as mqtt
import shutil
import sys
import time
import threading

class MQTTClient(mqtt.Client):
    """MQTT Agent implementation."""

    SUBSCRIBE_TIMEOUT = 10

    def __init__(self, broker, port, topics, name):
        super().__init__()

        self._log = logging.getLogger(name)
        self.broker = broker
        self.topics = topics
        self.port = int(port or 1883)

        self._subscribed = threading.Event()

    def on_connect(self, client, userdata, flags, rc):
        """On connect, subscribe to all topics."""
        self._subscribe_topics()

    def _subscribe_topics(self):
        """Suscribe to topics."""
        subtopics = self._subscribable_topics()
        # No subscribe to do
        if not subtopics:
            self._subscribed.set()
            return

        for topic in subtopics:
            self._log.info('Subscribing to: %s' % (topic.topic,))

        self._log_sub_topic(subtopics)

        topics = (t.subscribe_topic for t in subtopics)
        topics_list = [(t, 0) for t in topics]

        self._subscribed.clear()
        self.subscribe(topics_list)

    def _subscribable_topics(self):
        """Topics that are subscrible."""
        return [t for t in self.topics if t.subscribe_topic is not None]

    def on_subscribe(self, client, userdata, flags, rc):
        """Unlock '_subscribed' event."""
        self._subscribed.set()

    def on_message(self, client, userdata, msg):
        self._log.error("on_message({}): {}".format(
            msg.topic, msg.payload))

    def start(self):
        """Start MQTT Agent."""
        self._register_topics_callbacks()

        self.connect(self.broker)
        self.loop_start()

        subscribed = self._subscribed.wait(self.SUBSCRIBE_TIMEOUT)
        if not subscribed:
            raise RuntimeError('Topics subscribe timeout')

    def _register_topics_callbacks(self):
        """Register the callbacks for topics."""
        topics = (t for t in self.topics if t.callback is not None)
        for topic in topics:
            self.message_callback_add(
                topic.subscribe_topic, topic.callback)

    def stop(self):
        """Stop MQTT Agent."""
        self.loop_stop()

        subscribed = self._subscribed.wait(self.SUBSCRIBE_TIMEOUT)
        if not subscribed:
            raise RuntimeError('Topics subscribe timeout')

    @contextlib.contextmanager
    def message_callback(self, topic, callback):
        """Contextmanager that sets topic callback for current context."""
        self.message_callback_add(topic, callback)
        try:
            yield
        finally:
            self.message_callback_remove(topic)

    def publisher(self, topic):
        """Return a function that publishes on ``topic``."""
        return functools.partial(self.publish, topic)

    def publish(self, topic, payload=None, qos=0, retain=False):
        """Publish but requires strings to be bytes."""
        payload = self._bytes_safe_payload(payload)
        return super().publish(topic, payload=payload, qos=qos, retain=retain)

    @staticmethod
    def _bytes_safe_payload(payload):
        """Convert 'payload' to be a bytearray if type 'bytes'.
        Reject 'str' as it allows not managing encoding.
        paho-mqtt (1.2) does not correctly handles python2 'str'
        (== python3 bytes) and tries to encode them to 'utf-8'.
        Giving a ``bytearray`` circumvents it.
        https://github.com/eclipse/paho.mqtt.python/issues/125
        """
        assert not isinstance(payload, str)
        if isinstance(payload, bytes):
            return bytearray(payload)
        return payload


class Topic(object):
    """Topic base class."""
    LEVEL = r'(?P<%s>[^/]+)'

    def __init__(self, topic, callback=None):
        self.topic = topic
        self.fields = common.topic_fields(self.topic)
        self.subscribe_topic = self._topic_wildcard(self.topic, *self.fields)
        self.match_re = self._topic_match_re(self.topic, *self.fields)
        self.callback = self.wrap_callback(callback) if callback else None

    def fields_values(self, topic):
        """Extract named fields values from actual topic."""
        match = self.match_re.match(topic)
        fields = {f: match.group(f) for f in self.fields}
        return fields

    def wrap_callback(self, callback):
        """Wrap callback to call it with fields arguments values."""
        @functools.wraps(callback)
        def _wrapper(mqttc, obj, msg):  # pylint:disable=unused-argument
            fields = self.fields_values(msg.topic)
            return callback(msg, **fields)

        return _wrapper

    @staticmethod
    def _topic_wildcard(topic, *fields):
        """Convert `topic` named `fields` to '+' mqtt wildcard."""
        fmts = {f: '+' for f in fields}
        return topic.format(**fmts)

    @classmethod
    def _topic_match_re(cls, topic, *fields):
        """Convert `topic` to a re pattern that extracts fields values."""
        fmts = {f: cls.LEVEL % f for f in fields}
        return re.compile(topic.format(**fmts))
