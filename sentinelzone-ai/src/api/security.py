"""Authenticate HTTP and WebSocket traffic before routing in production."""
import secrets
import re
from starlette.responses import JSONResponse

MEDIA_ID = r'[A-Za-z0-9_:%-]{1,96}'
PUBLIC_SHOWCASE = re.compile(
    r'/api/v1/showcase/(?:'
    r'sources'
    r'|media(?:-summary)?'
    rf'|media/{MEDIA_ID}'
    rf'|media/{MEDIA_ID}/frames/[0-9]+'
    rf'|media/{MEDIA_ID}/file'
    r'|datasets'
    rf'|datasets/{MEDIA_ID}/manifest'
    rf'|demo/{MEDIA_ID}/frames/[0-9]+'
    r')'
)


def is_public_showcase(path: str) -> bool:
    """Read-only showcase routes that stay open in front of the token middleware."""
    return bool(PUBLIC_SHOWCASE.fullmatch(path))


class TokenAuthMiddleware:
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        public_demo = scope['type']=='http' and scope.get('method')=='GET' and is_public_showcase(scope.get('path',''))
        if public_demo or scope['type'] not in ('http', 'websocket') or (scope.get('path') in ('/health', '/', '/setup') or scope.get('path', '').startswith('/assets/')):
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        supplied = headers.get(b'authorization', b'')
        if not secrets.compare_digest(supplied, ('Bearer ' + self.token).encode('utf-8')):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await JSONResponse({'detail': 'Authentication required'}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)
