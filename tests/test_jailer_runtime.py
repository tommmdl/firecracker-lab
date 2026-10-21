import copy
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

spec=importlib.util.spec_from_file_location('jailer_runtime',Path(__file__).resolve().parents[1]/'scripts/jailer_runtime.py')
runtime=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.threads=json.loads((Path(__file__).parent/'fixtures/jailer-running-threads.json').read_text())

    def test_recorded_running_snapshot_is_approved(self):
        self.assertTrue(runtime.runtime_is_hardened(self.threads,65534,65534))

    def test_one_unprotected_thread_fails_whole_snapshot(self):
        for key,value in [('Uid','0 0 0 0'),('Gid','0 0 0 0'),('Groups','0'),('CapEff','0000000000000001'),('CapPrm','0000000000000001'),('CapInh','0000000000000001'),('CapAmb','0000000000000001'),('NoNewPrivs','0'),('Seccomp','0')]:
            with self.subTest(key=key):
                rows=copy.deepcopy(self.threads)
                rows[next(iter(rows))][key]=value
                self.assertFalse(runtime.runtime_is_hardened(rows,65534,65534))

    def test_preboot_or_missing_observation_is_not_approved(self):
        rows=dict(list(self.threads.items())[:2])
        self.assertFalse(runtime.runtime_is_hardened(rows,65534,65534))
        rows=copy.deepcopy(self.threads);del rows[next(iter(rows))]['Seccomp']
        self.assertFalse(runtime.runtime_is_hardened(rows,65534,65534))


class APIReadinessTests(unittest.TestCase):
    def test_socket_created_thread_is_not_ready(self):
        self.assertFalse(runtime.api_filter_ready({'2':{'Name':'firecracker','Seccomp':'0','NoNewPrivs':'1'}}))
    def test_named_but_unfiltered_api_is_not_ready(self):
        self.assertFalse(runtime.api_filter_ready({'2':{'Name':'fc_api','Seccomp':'0','NoNewPrivs':'1'}}))
        self.assertFalse(runtime.api_filter_ready({'2':{'Name':'fc_api','Seccomp':'2','NoNewPrivs':'0'}}))
    def test_filtered_api_is_ready(self):
        self.assertTrue(runtime.api_filter_ready({'2':{'Name':'fc_api','Seccomp':'2','NoNewPrivs':'1'}}))


class APIFramingTests(unittest.TestCase):
    def exchange(self,response,keep_open=False):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'api.socket')
            listener=socket.socket(socket.AF_UNIX);listener.bind(path);listener.listen(1)
            finished=threading.Event()
            def server():
                with listener.accept()[0] as client:
                    client.recv(8192)
                    client.sendall(response)
                    if keep_open:finished.wait(2)
            thread=threading.Thread(target=server);thread.start();started=time.monotonic()
            try:
                result=runtime.request(path,'GET','/')
                return result,time.monotonic()-started
            finally:
                finished.set();thread.join(timeout=3);listener.close()

    def test_content_length_finishes_without_connection_eof(self):
        result,elapsed=self.exchange(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 4\r\n\r\nnope',True)
        self.assertEqual(result,(400,'nope'))
        self.assertLess(elapsed,1.5)

    def test_truncated_response_is_not_accepted(self):
        with self.assertRaises(ConnectionError):
            self.exchange(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 8\r\n\r\nnope')


if __name__=='__main__':unittest.main()
