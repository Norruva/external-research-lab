"""Single-attempt public HTTPS acquisition; no requests occur on import."""
import hashlib
import os
import urllib.parse
import urllib.request


def allowed_url(url):
    p = urllib.parse.urlsplit(url)
    try:
        port = p.port
    except ValueError:
        return False
    return (p.scheme == 'https' and not p.username and not p.password and port in (None, 443)
            and p.hostname is not None and
            (p.hostname == 'huggingface.co' or p.hostname.endswith('.huggingface.co')
             or p.hostname.endswith('.hf.co')))


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError('Redirect outside reviewed public HTTPS hosts')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(split, pin, target):
    if not allowed_url(pin['url']):
        raise ValueError('Unreviewed initial URL')
    opener = urllib.request.build_opener(PublicRedirects())
    request = urllib.request.Request(pin['url'], headers={'User-Agent': 'EIDOS-public-research/1.0'})
    assert not any(k.lower() in ('authorization', 'cookie') for k in request.headers)
    partial = target.with_suffix('.part')
    if target.exists() or target.is_symlink():
        raise FileExistsError('Existing acquisition target')
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    digest, count = hashlib.sha256(), 0
    with os.fdopen(fd, 'wb') as output, opener.open(request, timeout=20) as response:
        if response.status != 200 or not allowed_url(response.url):
            raise ValueError('Unreviewed response')
        while True:
            block = response.read(1024**2)
            if not block:
                break
            count += len(block)
            if count > pin['bytes']:
                raise ValueError('Response size cap')
            output.write(block)
            digest.update(block)
        if count != pin['bytes'] or digest.hexdigest() != pin['sha256']:
            raise ValueError('Response size or SHA mismatch')
        final_host = urllib.parse.urlsplit(response.url).hostname
    # Hard-link publication refuses a target that appeared during the read.
    os.link(partial, target, follow_symlinks=False)
    partial.unlink()
    return {'split': split, 'complete': True, 'bytes': count, 'sha256': digest.hexdigest(),
            'final_host': final_host, 'account_credentials_or_token_endpoint_used': False}
