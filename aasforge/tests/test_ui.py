"""The web editor in a real browser (Playwright/Chromium via Node), against a server with tier rules."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import Server

import battery_passport
from aasforge.dpp import passport

NODE = shutil.which('node')
SCRIPT = Path(__file__).with_name('ui_e2e.js')


def playwright_available():
    if not NODE:
        return False
    return subprocess.run([NODE, '-e', "require('playwright')"], capture_output=True).returncode == 0


@unittest.skipUnless(playwright_available(), 'needs node with playwright')
class WebEditor(unittest.TestCase):
    def test_browse_edit_create_query(self):
        server = Server(access_rules='dpp', api_keys={'op-key': {'sub': 'operator', 'dpp_tier': 'economic-operator'}})
        try:
            passport.create(server.forge.repository, battery_passport.spec())
            server.forge.repository.save('submodel', {'modelType': 'Submodel', 'id': 'urn:example:ui:device',
                                                      'idShort': 'Device', 'submodelElements': [
                {'modelType': 'Property', 'idShort': 'Temperature', 'valueType': 'xs:double', 'value': '20'}]})
            run = subprocess.run([NODE, str(SCRIPT), server.base, 'op-key'], capture_output=True, text=True, timeout=180,
                                 cwd=str(SCRIPT.parent))
            report = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {'error': run.stderr}
            self.assertTrue(report.get('ok'), report)
            steps = report['steps']
            self.assertIn("script-src 'self'", steps['csp'])
            self.assertGreaterEqual(steps['shells'], 1)
            self.assertTrue(steps['anonymousSaveRefused'])
            self.assertEqual(server.forge.store.get('submodel', 'urn:example:ui:device')['submodelElements'][0]['value'], '33.5')
            self.assertTrue(server.forge.store.exists('submodel', 'urn:example:ui:pcf'))
            self.assertIn('result', steps['query'])
            self.assertGreaterEqual(steps['revisions'], 2)
            self.assertEqual([e for e in report['consoleErrors'] if 'Failed to load resource' not in e], [])
        finally:
            server.close()


if __name__ == '__main__':
    unittest.main()
