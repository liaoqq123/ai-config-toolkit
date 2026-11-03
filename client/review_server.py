"""Local preview review UI. Approvals never automatically apply workbooks."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from urllib.parse import parse_qs, urlsplit
from config_toolkit.core.project import load_project
from config_toolkit.core.workflow import Workflow


def make_handler(workflow, token):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, value):
            raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path == '/':
                raw = (Path(__file__).with_name('review.html').read_text(encoding='utf-8')
                       .replace('__REVIEW_TOKEN__', token)).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(raw)
                return
            try:
                if route.path == '/api/previews':
                    return self.send(200, workflow.list_previews())
                if route.path == '/api/preview':
                    identifier = parse_qs(route.query).get('id', [''])[0]
                    return self.send(200, workflow.get_preview(identifier))
                self.send(404, {'error':'不存在的路径。'})
            except Exception as error:
                self.send(400, {'error':str(error)})

        def do_POST(self):
            expected = 'http://127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Origin') != expected or self.headers.get('X-Review-Token') != token:
                return self.send(403, {'error':'需要在本机确认页面操作。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.path != '/api/approve' or not 0 < length <= 16384:
                    return self.send(400, {'error':'请求路径或长度无效。'})
                payload = json.loads(self.rfile.read(length))
                if payload.get('confirmed') is not True:
                    raise ValueError('请核对本表差异并勾选确认。')
                actor, reason = payload.get('actor', '').strip(), payload.get('reason', '').strip()
                if not 1 <= len(actor) <= 80 or not 1 <= len(reason) <= 1000:
                    raise ValueError('请填写确认人和确认说明。')
                result = workflow.approve(payload['preview_id'], actor, reason)
                self.send(200, result)
            except Exception as error:
                self.send(400, {'error':str(error)})

        def log_message(self, format, *args):
            return
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    workflow = Workflow(load_project(args.project), args.root)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(workflow, secrets.token_urlsafe(32)))
    print('逐表确认页面：http://127.0.0.1:' + str(server.server_port), flush=True)
    print('页面只记录人工确认；实际写入需另行 apply。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
