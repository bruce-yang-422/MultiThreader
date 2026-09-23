import time
import unittest

from test_app import AppTests
from multithreader.control import set_draining, start_heartbeat, status
from multithreader.db import get_db
from multithreader.publishing import claim_job, claim_reply, publish_job


class ControlTests(unittest.TestCase):
    setUp = AppTests.setUp
    tearDown = AppTests.tearDown
    login = AppTests.login
    payload = AppTests.payload
    submit = AppTests.submit
    jobs = AppTests.jobs
    def test_drain_preserves_queue_and_rejects_new_submissions(self):
        batch = self.submit()
        with self.app.app_context():
            set_draining(True)
            self.assertIsNone(claim_job())
            self.assertIsNone(claim_reply())
            self.assertEqual(status()['active'], 0)
        self.assertEqual(self.client.post('/api/batches', json=self.payload()).status_code, 503)
        self.assertEqual(self.client.post('/api/jobs/anything/retry').status_code, 503)
        self.assertEqual(self.client.post('/api/jobs/anything/reply/retry').status_code, 503)
        self.assertTrue(all(job['status'] == 'pending' for job in self.jobs(batch)))
        with self.app.app_context():
            set_draining(False)
            self.assertIsNotNone(claim_job())

    def test_drain_allows_claimed_publication_to_finish(self):
        self.submit()
        with self.app.app_context():
            job = claim_job()
            set_draining(True)
            self.assertEqual(status()['active'], 1)
            publish_job(job)
            self.assertEqual(status()['active'], 0)
            self.assertIsNone(claim_job())

    def test_heartbeat_is_independent_and_health_is_minimal(self):
        stop, thread = start_heartbeat(self.app)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with self.app.app_context():
                    if status()['worker_online']:
                        break
                time.sleep(.05)
            response = self.client.get('/healthz')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json), {'application', 'protocol', 'instance', 'ready', 'worker_online'})
            self.assertTrue(response.json['worker_online'])
            with self.app.app_context():
                set_draining(True)
            self.assertFalse(self.client.get('/healthz').json['ready'])
        finally:
            stop.set()
            thread.join(5)


del AppTests
