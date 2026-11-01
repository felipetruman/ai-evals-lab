"""Offline transport tests using official OTLP protobuf over real localhost HTTP."""
import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from evals_lab.remote import AuditOTLPExporter, reconcile_collector_file, run_remote
from evals_lab.otel_sdk_demo import execute_sdk_demo

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / 'examples/policy.json').read_text())


class Receiver:
    def __init__(self, out: Path, fault='none'):
        self.out = out
        self.fault = fault
        self.received = 0
        self.requests = 0
        self.httpd = None
        self.thread = None

    def __enter__(self):
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                receiver.requests += 1
                size = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(size)
                if self.path != '/v1/traces':
                    self.send_response(404); self.end_headers(); return
                if receiver.fault == 'bad_request':
                    self.send_response(400); self.end_headers(); return
                if receiver.fault == 'transient' and receiver.requests == 1:
                    self.send_response(503); self.end_headers(); return
                msg = ExportTraceServiceRequest()
                msg.ParseFromString(raw)
                # Simulates OTLP Collector file exporter, not a real Collector.
                from google.protobuf.json_format import MessageToDict
                doc = MessageToDict(msg, preserving_proto_field_name=False)
                count = sum(len(scope.get('spans', [])) for resource in doc['resourceSpans']
                            for scope in resource['scopeSpans'])
                if receiver.fault == 'silent_loss':
                    doc['resourceSpans'][0]['scopeSpans'][0]['spans'].pop()
                    count -= 1
                if receiver.fault != 'silent_ack':
                    receiver.out.write_text(json.dumps(doc) + '\n')
                    receiver.received += count
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-protobuf')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *_):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return f'http://127.0.0.1:{self.httpd.server_port}/v1/traces'

    def __exit__(self, *_):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)


class TestRemoteTransport(unittest.TestCase):
    def test_end_to_end_http_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            with Receiver(path) as endpoint:
                report = run_remote(endpoint, POLICY, path)
            self.assertTrue(report['passed'], report)
            self.assertEqual(report['receipt']['received_spans'], 4)
            self.assertEqual(report['network']['successful_batches'], 1)
            self.assertEqual(report['local']['effects']['refunded'], 1)

    def test_retries_transient_503(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            receiver = Receiver(path, 'transient')
            with receiver as endpoint:
                report = run_remote(endpoint, POLICY, path, timeout=4)
            self.assertTrue(report['passed'], report)
            self.assertGreaterEqual(receiver.requests, 2)

    def test_permanent_400_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            receiver = Receiver(path, 'bad_request')
            with receiver as endpoint:
                report = run_remote(endpoint, POLICY, path, timeout=1)
            self.assertFalse(report['passed'])
            self.assertEqual(receiver.requests, 1)
            self.assertEqual(report['network']['failed_batches'], 1)

    def test_http_ack_without_persistence_must_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            with Receiver(path, 'silent_ack') as endpoint:
                report = run_remote(endpoint, POLICY, path)
            self.assertFalse(report['passed'])
            self.assertEqual(report['network']['successful_batches'], 1)
            self.assertIn('COLLECTOR_FILE_MISSING', report['receipt']['reasons'])

    def test_silent_loss_must_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            with Receiver(path, 'silent_loss') as endpoint:
                report = run_remote(endpoint, POLICY, path)
            self.assertFalse(report['passed'])
            self.assertIn('COLLECTOR_SPANS_MISSING', report['receipt']['reasons'])
            self.assertEqual(report['receipt']['received_spans'], 3)

    def test_network_unreachable_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            with Receiver(path) as endpoint:
                pass  # server is now shut down: explicit connection refused
            report = run_remote(endpoint, POLICY, path, timeout=1)
            self.assertFalse(report['passed'])
            self.assertEqual(report['network']['successful_batches'], 0)

    def test_receiver_receipt_detects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            run = execute_sdk_demo()
            doc = copy.deepcopy(run.otlp)
            doc['resourceSpans'][0]['scopeSpans'][0]['spans'].append(
                copy.deepcopy(doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]))
            path.write_text(json.dumps(doc) + '\n')
            report = reconcile_collector_file(run.attestation, path)
            self.assertFalse(report.complete)
            self.assertIn('DUPLICATE_COLLECTOR_SPAN', report.reasons)

    def test_collector_extra_unexpected_span(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            run = execute_sdk_demo()
            doc = copy.deepcopy(run.otlp)
            doc['resourceSpans'][0]['scopeSpans'][0]['spans'].append(
                {**doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0], 'spanId':'e'*16})
            path.write_text(json.dumps(doc) + '\n')
            report = reconcile_collector_file(run.attestation, path)
            self.assertIn('UNEXPECTED_COLLECTOR_SPANS', report.reasons)

    def test_foreign_traces_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            run = execute_sdk_demo()
            doc = copy.deepcopy(run.otlp)
            other = copy.deepcopy(doc)
            for span in other['resourceSpans'][0]['scopeSpans'][0]['spans']:
                span['traceId'] = 'e'*32
            path.write_text(json.dumps(other) + '\n' + json.dumps(doc) + '\n')
            self.assertTrue(reconcile_collector_file(run.attestation, path).complete)

    def test_bad_collector_data_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            run = execute_sdk_demo()
            path.write_text('{broken json\n')
            with self.assertRaisesRegex(ValueError, 'line 1'):
                reconcile_collector_file(run.attestation, path)

    def test_endpoint_validation(self):
        for endpoint in ('file:///tmp', 'http://127.0.0.1:4318/',
                         'http://foo:password@localhost:4318/v1/traces'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                AuditOTLPExporter(endpoint)

    def test_safety_bypass_remains_blocked_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collector.jsonl'
            with Receiver(path) as endpoint:
                report = run_remote(endpoint, POLICY, path, skip_identity=True)
            self.assertFalse(report['passed'])
            self.assertTrue(report['receipt']['complete'])
            self.assertEqual(report['local']['effects']['refunded'], 0)


if __name__ == '__main__':
    unittest.main()
