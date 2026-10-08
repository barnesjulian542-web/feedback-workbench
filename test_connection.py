import os
import unittest
from unittest.mock import patch
import httpx2 as httpx
from openai import DefaultHttpxClient
import workflow


class ConnectionFixTests(unittest.TestCase):
    def test_connection_failure_retries_and_client_closes_without_proxy(self):
        calls = []
        clients = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError('temporary failure', request=request)
            return httpx.Response(200, json={
                'id':'test-response','object':'chat.completion','created':0,'model':'test',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant',
                    'content':'{"category":"bug","title":"上传失败","problem":"上传失败","evidence":["上传失败"],"keywords":["上传"],"acceptance_criteria":["上传恢复"],"needs_clarification":false}'}}],
                'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}})
        def client_factory(**kwargs):
            self.assertFalse(kwargs['trust_env'])
            client = DefaultHttpxClient(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client
        with patch.dict(os.environ, {'LLM_API_KEY':'offline-test','LLM_BASE_URL':'https://example.invalid/v1',
                                     'LLM_MODEL':'test','LLM_TRUST_ENV':'false','HTTPS_PROXY':'http://127.0.0.1:1'}), \
             patch('openai.DefaultHttpxClient', side_effect=client_factory):
            analysis, metadata = workflow.real_analysis('上传失败')
        self.assertEqual(analysis.category,'bug')
        self.assertEqual(len(calls),2)
        self.assertTrue(clients[0].is_closed)
        self.assertEqual(metadata['prompt_tokens'],2)

    def test_error_does_not_expose_key_and_closes_client(self):
        clients=[]
        def handler(request):
            raise httpx.ConnectError('SECRET_TEST_VALUE',request=request)
        def factory(**kwargs):
            client=DefaultHttpxClient(transport=httpx.MockTransport(handler),**kwargs)
            clients.append(client)
            return client
        with patch.dict(os.environ,{'LLM_API_KEY':'SECRET_TEST_VALUE','LLM_BASE_URL':'https://example.invalid/v1',
                                   'LLM_MODEL':'test','LLM_TRUST_ENV':'true'}), \
             patch('openai.DefaultHttpxClient',side_effect=factory):
            with self.assertRaises(RuntimeError) as caught:
                workflow.real_analysis('上传失败')
        self.assertNotIn('SECRET_TEST_VALUE',str(caught.exception))
        self.assertTrue(clients[0].is_closed)


if __name__=='__main__':
    unittest.main()
