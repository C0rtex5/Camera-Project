"""Bounded RTSP DESCRIBE check with separate credentials and safe diagnostics."""
import base64
import hashlib
import secrets
import socket
import ssl
import time
from urllib.parse import urlsplit
from urllib.request import parse_http_list, parse_keqv_list


def _authorization(challenge, credentials, uri):
    username, password = credentials['username'], credentials['password']
    if challenge.lower().startswith('basic '):
        return 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()
    if not challenge.lower().startswith('digest '):
        raise ValueError('unsupported_auth')
    fields = parse_keqv_list(parse_http_list(challenge[7:]))
    algorithm = fields.get('algorithm', 'MD5').upper()
    if algorithm not in ('MD5', 'MD5-SESS', 'SHA-256', 'SHA-256-SESS'):
        raise ValueError('unsupported_auth')
    digest = hashlib.sha256 if algorithm.startswith('SHA-256') else hashlib.md5
    def hashed(value):
        return digest(value.encode()).hexdigest()
    nonce, realm = fields['nonce'], fields.get('realm', '')
    cnonce, nc = secrets.token_hex(16), '00000001'
    ha1 = hashed(f'{username}:{realm}:{password}')
    if algorithm.endswith('-SESS'):
        ha1 = hashed(f'{ha1}:{nonce}:{cnonce}')
    ha2 = hashed(f'DESCRIBE:{uri}')
    qops = fields.get('qop', '').split(',')
    qop = 'auth' if 'auth' in [q.strip() for q in qops] else ''
    if fields.get('qop') and not qop:
        raise ValueError('unsupported_auth')
    response = hashed(f'{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}' if qop else f'{ha1}:{nonce}:{ha2}')
    values = dict(username=username, realm=realm, nonce=nonce, uri=uri, response=response)
    if fields.get('opaque'):
        values['opaque'] = fields['opaque']
    if qop or algorithm.endswith('-SESS'):
        values['cnonce'] = cnonce
    if any(ord(c)<32 or ord(c)==127 for value in values.values() for c in value):
        raise ValueError('unsupported_auth')
    quote = lambda value: '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    result = 'Digest ' + ', '.join(f'{key}={quote(value)}' for key, value in values.items())
    result += f', algorithm={algorithm}'
    if qop:
        result += f', qop={qop}, nc={nc}'
    return result


def check_rtsp(url, credentials=None, timeout=4):
    """No credentials/URLs or remote error strings are returned or logged."""
    start = time.monotonic()
    parsed = urlsplit(url)
    port = parsed.port or (322 if parsed.scheme == 'rtsps' else 554)
    def exchange(sequence, authorization=None):
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise TimeoutError
        with socket.create_connection((parsed.hostname, port), timeout=remaining) as raw:
            conn = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname) if parsed.scheme == 'rtsps' else raw
            with conn:
                headers = [f'DESCRIBE {url} RTSP/1.0', f'CSeq: {sequence}', 'Accept: application/sdp', 'User-Agent: Camera-Project']
                if authorization:
                    headers.append('Authorization: ' + authorization)
                conn.sendall(('\r\n'.join(headers)+'\r\n\r\n').encode())
                data = b''
                while b'\r\n\r\n' not in data:
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        raise TimeoutError
                    conn.settimeout(remaining)
                    chunk = conn.recv(4096)
                    if not chunk or len(data)+len(chunk)>32768:
                        raise ValueError('invalid_response')
                    data += chunk
                lines = data.split(b'\r\n\r\n', 1)[0].decode('utf-8', errors='replace').split('\r\n')
                if not lines[0].startswith('RTSP/'):
                    raise ValueError('invalid_response')
                status = int(lines[0].split()[1])
                challenges = [line.split(':',1)[1].strip() for line in lines[1:] if line.lower().startswith('www-authenticate:')]
                return status, challenges
    messages = {
        'accepted': 'RTSP aceito. A confirmação de vídeo aparece no estado da câmera.',
        'authentication_required': 'A câmera exige usuário e senha RTSP.',
        'authentication_rejected': 'A câmera recusou o login RTSP. Verifique a conta, a senha e a permissão de vídeo.',
        'path_not_found': 'Caminho RTSP não encontrado. Confira a URL do canal da câmera.',
        'forbidden': 'A câmera negou acesso ao canal RTSP.',
        'timeout': 'A câmera não respondeu no prazo. Confira a VPN e a porta RTSP.',
        'network_unreachable': 'Não foi possível alcançar a câmera a partir deste servidor.',
        'tls_error': 'Não foi possível validar a conexão RTSP segura.',
        'unsupported_auth': 'A câmera exige um método de autenticação não suportado pelo teste.',
        'invalid_response': 'A porta não respondeu com um protocolo RTSP válido.',
        'rtsp_error': 'A câmera retornou um erro RTSP.'}
    status = None
    try:
        status, challenges = exchange(1)
        if status == 401 and credentials:
            challenge = next((c for c in challenges if c.lower().startswith('digest ')), challenges[0] if challenges else '')
            status, _ = exchange(2, _authorization(challenge, credentials, url))
        code = 'accepted' if 200 <= status < 300 else {401: 'authentication_rejected' if credentials else 'authentication_required',403:'forbidden',404:'path_not_found'}.get(status,'rtsp_error')
    except (TimeoutError, socket.timeout):
        code = 'timeout'
    except ssl.SSLError:
        code = 'tls_error'
    except OSError:
        code = 'network_unreachable'
    except (ValueError, KeyError, IndexError):
        code = 'unsupported_auth' if status == 401 and credentials else 'invalid_response'
    return {'ok': code=='accepted', 'code':code, 'message':messages[code], 'rtsp_status':status}
