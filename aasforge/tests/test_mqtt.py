"""MQTT bridge against a real broker (mosquitto): BaSyx topics, tier-filtered payloads, retained
ValueOnly, no messages for hidden changes, and authorised telemetry ingest."""
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import database

import battery_passport
from aasforge import canonical
from aasforge.app import Config, Forge
from aasforge.dpp import passport

try:
    import paho.mqtt.client as paho
except ImportError:  # pragma: no cover
    paho = None

from aasforge.mqtt import MqttBridge

MOSQUITTO = shutil.which('mosquitto') or ('/usr/sbin/mosquitto' if Path('/usr/sbin/mosquitto').exists() else None)
DEVICE = 'urn:example:mqtt:device'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipIf(paho is None or MOSQUITTO is None, 'needs paho-mqtt and a mosquitto binary')
class MqttBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        config = Path(cls.tmp.name) / 'mosquitto.conf'
        config.write_text(f'listener {cls.port} 127.0.0.1\nallow_anonymous true\npersistence false\n')
        cls.broker = subprocess.Popen([MOSQUITTO, '-c', str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(('127.0.0.1', cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        cls.forge = Forge(Config(database=database(), access_rules='dpp'))
        cls.forge.repository.save('submodel', {'modelType': 'Submodel', 'id': DEVICE, 'idShort': 'Device',
                                               'submodelElements': [{'modelType': 'Property', 'idShort': 'Temperature',
                                                                     'valueType': 'xs:double', 'value': '20.0'}]})
        cls.bridge = MqttBridge(cls.forge, f'mqtt://127.0.0.1:{cls.port}', 'test',
                                ingest_claims={'sub': 'telemetry', 'dpp_tier': 'economic-operator'}).start()
        cls.messages, cls.lock = [], threading.Lock()
        cls.listener = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id='test-listener')
        ready = threading.Event()
        cls.listener.on_connect = lambda c, u, f, r, p: (c.subscribe('#', qos=1), ready.set())

        def on_message(client, userdata, message):
            with cls.lock:
                cls.messages.append((message.topic, message.payload, message.retain))
        cls.listener.on_message = on_message
        cls.listener.connect('127.0.0.1', cls.port)
        cls.listener.loop_start()
        ready.wait(10)
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.listener.loop_stop()
        cls.listener.disconnect()
        cls.bridge.stop()
        cls.broker.terminate()
        cls.broker.wait(10)
        cls.forge.store.db.drop_schema()
        cls.tmp.cleanup()

    def drain(self, wait=0.6):
        self.bridge.flush()
        time.sleep(wait)
        with self.lock:
            messages, self.messages[:] = list(self.messages), []
        return messages

    def late_subscriber(self, topic):
        got, ready = [], threading.Event()
        client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id='late')
        client.on_connect = lambda c, u, f, r, p: c.subscribe(topic, qos=1)
        client.on_message = lambda c, u, m: (got.append((m.payload, m.retain)), ready.set())
        client.connect('127.0.0.1', self.port)
        client.loop_start()
        ready.wait(5)
        client.loop_stop()
        client.disconnect()
        return got

    def test_passport_lifecycle(self):
        created = passport.create(self.forge.repository, battery_passport.spec())
        messages = self.drain()
        topics = [t for t, _, _ in messages]
        self.assertIn('aas-repository/test/shells/created', topics)
        self.assertIn('sm-repository/test/submodels/created', topics)
        everything = b' '.join(p for _, p, _ in messages)
        self.assertNotIn(b'RemainingCapacity', everything)          # legitimate interest
        hidden = next(i for i in created['submodels'] if i.endswith(':2'))
        self.assertNotIn(canonical.b64url_encode(hidden), ' '.join(topics))   # authority-only submodel
        condition = next(i for i in created['submodels'] if i.endswith('dbp-product-condition'))
        b64 = canonical.b64url_encode(condition)
        self.assertIn(f'sm-repository/test/submodels/{b64}/$value', topics)
        retained = self.late_subscriber(f'sm-repository/test/submodels/{b64}/$value')
        self.assertTrue(retained and retained[0][1])            # a newcomer gets the current values at once
        self.assertNotIn(b'RemainingCapacity', retained[0][0])

        # a change only inside hidden data publishes nothing
        def hidden_change(submodel):
            for path, element in __import__('aasforge.elements', fromlist=['x']).walk_elements(submodel):
                if path.endswith('RemainingCapacityValue'):
                    element['value'] = '1.0'
        self.forge.repository.update_submodel(condition, hidden_change)
        self.assertEqual([t for t, _, _ in self.drain() if b64 in t], [])

        # deleting a visible submodel clears its retained value
        self.forge.repository.remove('submodel', condition)
        messages = self.drain()
        self.assertIn(f'sm-repository/test/submodels/{b64}/deleted', [t for t, _, _ in messages])
        self.assertTrue(any(t.endswith(f'{b64}/$value') and p == b'' for t, p, _ in messages))

    def test_element_update_topic_and_ingest(self):
        self.drain(0.1)
        b64 = canonical.b64url_encode(DEVICE)
        publisher = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id='device')
        publisher.connect('127.0.0.1', self.port)
        publisher.loop_start()
        publisher.publish(f'aasforge/test/submodels/{b64}/submodelElements/Temperature/set', json.dumps(23.75), qos=1).wait_for_publish(5)
        for _ in range(50):
            if self.forge.store.get('submodel', DEVICE)['submodelElements'][0]['value'] == '23.75':
                break
            time.sleep(0.1)
        publisher.loop_stop()
        publisher.disconnect()
        self.assertEqual(self.forge.store.get('submodel', DEVICE)['submodelElements'][0]['value'], '23.75')
        self.assertTrue(self.forge.store.history('submodel', DEVICE)[-1]['actor'].startswith('mqtt:'))
        messages = self.drain()
        element_topic = f'sm-repository/test/submodels/{b64}/submodelElements/Temperature/updated'
        self.assertIn(element_topic, [t for t, _, _ in messages])
        value = next(p for t, p, _ in messages if t.endswith(f'{b64}/$value'))
        self.assertEqual(json.loads(value), {'Temperature': 23.75})

    def test_ingest_is_authorised(self):
        b64 = canonical.b64url_encode(DEVICE)
        topic = f'aasforge/test/submodels/{b64}/submodelElements/Temperature/set'
        claims = self.bridge.ingest_claims
        try:
            self.bridge.ingest_claims = {'sub': 'stranger', 'dpp_tier': 'public'}
            self.assertFalse(self.bridge.ingest(topic, b'99'))
            self.bridge.ingest_claims = claims
            self.assertFalse(self.bridge.ingest(topic, b'"not a number"'))      # xs:double check
            self.assertFalse(self.bridge.ingest('aasforge/other/submodels/x/submodelElements/T/set', b'1'))
            self.assertTrue(self.bridge.ingest(topic, b'21'))
        finally:
            self.bridge.ingest_claims = claims


if __name__ == '__main__':
    unittest.main()
