"""Authored in-memory HTTP responses; no network or native dependencies."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from acquire import PublicRedirects, allowed_url, download


class Response(io.BytesIO):
    status = 200
    url = 'https://cas-bridge.xethub.hf.co/authored-fixture?ephemeral=not-recorded'


class AcquisitionTests(unittest.TestCase):
    def test_https_host_userinfo_and_port_bounds(self):
        for url in ('https://huggingface.co/datasets/test', 'https://cdn-lfs-us-1.hf.co/file'):
            self.assertTrue(allowed_url(url))
        for url in ('http://huggingface.co/file', 'https://huggingface.co.evil.invalid/file',
                    'https://evil.invalid.hf.co.evil.invalid/file', 'file:///tmp/file',
                    'https://name:secret@huggingface.co/file', 'https://huggingface.co:444/file',
                    'https://huggingface.co:bad/file'):
            with self.subTest(url=url): self.assertFalse(allowed_url(url))

    def test_redirect_refuses_unreviewed_hosts(self):
        request = urllib.request.Request('https://huggingface.co/fixture')
        with self.assertRaises(ValueError):
            PublicRedirects().redirect_request(request, None, 302, 'Found', {}, 'https://example.org/file')

    def run_download(self, payload, size=None, sha=None, status=200):
        body = b'Authored data fixture'
        pin = {'url': 'https://huggingface.co/fixture', 'bytes': len(body) if size is None else size,
               'sha256': hashlib.sha256(body).hexdigest() if sha is None else sha}
        response = Response(payload); response.status = status
        opener = unittest.mock.Mock(); opener.open.return_value = response
        with tempfile.TemporaryDirectory() as d, patch('acquire.urllib.request.build_opener', return_value=opener):
            target = Path(d) / 'train.parquet'
            if payload != body or status != 200 or size is not None or sha is not None:
                with self.assertRaises(ValueError): download('train', pin, target)
                self.assertFalse(target.exists())
                self.assertTrue(target.with_suffix('.part').exists())
            else:
                receipt = download('train', pin, target)
                self.assertEqual(target.read_bytes(), body)
                self.assertEqual(target.stat().st_mode & 0o777, 0o400)
                self.assertNotIn('ephemeral', str(receipt))
                self.assertEqual(receipt['final_host'], 'cas-bridge.xethub.hf.co')
                self.assertFalse(target.with_suffix('.part').exists())
                with self.assertRaises(FileExistsError): download('train', pin, target)
            # One attempt only; no retries or explicit token/auth request.
            self.assertEqual(opener.open.call_count, 1)
            request = opener.open.call_args.args[0]
            self.assertFalse({'authorization', 'cookie'} & {x.lower() for x in request.headers})

    def test_exact_bytes_and_receipt_excludes_redirect_query(self):
        self.run_download(b'Authored data fixture')

    def test_long_short_wrong_hash_and_error_responses_retained_without_publication(self):
        self.run_download(b'Authored data fixture EXTRA')
        self.run_download(b'Short')
        self.run_download(b'Authored data fixture', sha='0'*64)
        self.run_download(b'Authored data fixture', status=403)


if __name__ == '__main__': unittest.main()
