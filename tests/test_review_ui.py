import importlib.util
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest


class ReviewTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('review_server', Path(__file__).resolve().parents[1]/'client/review_server.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        class FakeWorkflow:
            def __init__(self):
                self.approvals = []
            def list_previews(self):
                return [{'preview_id':'unit_preview','file':'Fiction.xlsx','sheet':'配置'}]
            def get_preview(self, identifier):
                return {'preview_id':identifier,'before':'old','after':'new'}
            def approve(self, identifier, actor, reason):
                self.approvals.append((identifier,actor,reason))
                return {'approval_id':'unit_approval'}
        self.workflow = FakeWorkflow()
        self.server = ThreadingHTTPServer(('127.0.0.1',0),module.make_handler(self.workflow,'unit_token'))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, value=None, headers=None):
        connection = HTTPConnection('127.0.0.1',self.server.server_port)
        body = None if value is None else json.dumps(value).encode()
        connection.request(method,path,body=body,headers=headers or {})
        response = connection.getresponse()
        result = response.status,response.read()
        connection.close()
        return result

    def test_review_requires_same_origin_token_and_explicit_confirmation(self):
        payload = {'preview_id':'unit_preview','actor':'Human reviewer','reason':'checked','confirmed':True}
        status,_ = self.request('POST','/api/approve',payload)
        self.assertEqual(status,403)
        self.assertEqual(self.workflow.approvals,[])
        headers = {'Origin':'http://127.0.0.1:'+str(self.server.server_port),'X-Review-Token':'unit_token','Content-Type':'application/json'}
        status,_ = self.request('POST','/api/approve',{**payload,'confirmed':False},headers)
        self.assertEqual(status,400)
        status,body = self.request('POST','/api/approve',payload,headers)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['approval_id'],'unit_approval')
        self.assertEqual(len(self.workflow.approvals),1)

    def test_preview_page_and_read_only_listing(self):
        status,body = self.request('GET','/')
        self.assertEqual(status,200)
        self.assertNotIn(b'__REVIEW_TOKEN__',body)
        status,body = self.request('GET','/api/previews')
        self.assertEqual(json.loads(body)[0]['preview_id'],'unit_preview')
        self.assertEqual(self.workflow.approvals,[])


if __name__ == '__main__':
    unittest.main()
