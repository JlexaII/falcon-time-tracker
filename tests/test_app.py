import tempfile
import unittest
from pathlib import Path
from app import create_app


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / 'test.sqlite3')
        self.app = create_app({'TESTING': True, 'DATABASE': self.db_path})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def employee(self, name='Иван Тестов'):
        r = self.client.post('/api/employees', json={'name': name})
        self.assertEqual(r.status_code, 201)
        return r.json['id']

    def test_add_employee(self):
        self.employee()
        self.assertEqual(len(self.client.get('/api/employees').json), 1)
        self.assertEqual(self.client.post('/api/employees', json={'name': '  '}).status_code, 400)

    def test_start_stop_and_repeat_start_rejected(self):
        emp = self.employee()
        payload = {'employee_id': emp, 'description': 'Тестовая задача'}
        started = self.client.post('/api/timers/start', json=payload)
        self.assertEqual(started.status_code, 201)
        self.assertEqual(self.client.post('/api/timers/start', json=payload).status_code, 409)
        timer_id = started.json['id']
        self.assertEqual(len(self.client.get('/api/active').json), 1)
        self.assertEqual(self.client.post(f'/api/timers/{timer_id}/stop').status_code, 200)
        self.assertEqual(self.client.post(f'/api/timers/{timer_id}/stop').status_code, 409)
        self.assertEqual(len(self.client.get('/api/active').json), 0)
        self.assertEqual(self.client.post('/api/timers/start', json=payload).status_code, 201)

    def test_empty_report(self):
        self.employee()
        response = self.client.get('/api/report?from=2025-01-01&to=2025-01-31')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['entries'], [])
        self.assertEqual(response.json['employees'][0]['total_seconds'], 0)

    def test_report_period_overlap(self):
        emp = self.employee()
        self.client.post('/api/timers/start', json={'employee_id': emp, 'description': 'Работа'})
        r = self.client.get('/api/report?from=2020-01-01&to=2035-12-31')
        self.assertEqual(len(r.json['entries']), 1)
        self.assertTrue(r.json['entries'][0]['active'])
        self.assertEqual(self.client.get('/api/report?from=2025-03-05&to=2025-03-01').status_code, 400)

    def test_data_survives_restart(self):
        emp = self.employee()
        self.client.post('/api/timers/start', json={'employee_id': emp, 'description': 'После перезапуска'})
        new_client = create_app({'TESTING': True, 'DATABASE': self.db_path}).test_client()
        self.assertEqual(len(new_client.get('/api/employees').json), 1)
        self.assertEqual(len(new_client.get('/api/active').json), 1)


if __name__ == '__main__':
    unittest.main()
