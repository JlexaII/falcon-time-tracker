import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, render_template, request


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(DATABASE=str(Path(__file__).resolve().parent / 'tracker.sqlite3'))
    if test_config:
        app.config.update(test_config)

    @contextmanager
    def connect():
        """Open a SQLite transaction and always release the file handle.

        sqlite3.Connection.__exit__ commits/rolls back but does NOT close.
        Explicit close is essential on Windows when tests delete temp DB files.
        """
        db = sqlite3.connect(app.config['DATABASE'])
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys = ON')
            with db:
                yield db
        finally:
            db.close()

    def init_db():
        with connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL CHECK (length(trim(name)) > 0)
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL REFERENCES employees(id),
                    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
                    started_at TEXT NOT NULL,
                    stopped_at TEXT,
                    CHECK(stopped_at IS NULL OR stopped_at >= started_at)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_timer
                    ON entries(employee_id) WHERE stopped_at IS NULL;
            ''')
    init_db()

    def error(message, code=400):
        return jsonify(error=message), code

    def utc_now():
        return datetime.now(timezone.utc).isoformat(timespec='microseconds')

    def parse_date(value):
        return datetime.strptime(value, '%Y-%m-%d').date()

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/api/employees')
    def employees():
        with connect() as db:
            rows = db.execute('SELECT id, name FROM employees ORDER BY id').fetchall()
            return jsonify([dict(row) for row in rows])

    @app.post('/api/employees')
    def add_employee():
        data = request.get_json(silent=True) or {}
        name = str(data.get('name', '')).strip()
        if not name or len(name) > 100:
            return error('Имя обязательно (до 100 символов).')
        with connect() as db:
            cursor = db.execute('INSERT INTO employees(name) VALUES (?)', (name,))
            return jsonify(id=cursor.lastrowid, name=name), 201

    @app.post('/api/timers/start')
    def start_timer():
        data = request.get_json(silent=True) or {}
        try:
            employee_id = int(data.get('employee_id'))
        except (ValueError, TypeError):
            return error('Выберите сотрудника.')
        description = str(data.get('description', '')).strip()
        if not description or len(description) > 500:
            return error('Укажите задачу (до 500 символов).')
        with connect() as db:
            employee = db.execute('SELECT 1 FROM employees WHERE id = ?', (employee_id,)).fetchone()
            if not employee:
                return error('Сотрудник не найден.', 404)
            try:
                cursor = db.execute('INSERT INTO entries(employee_id, description, started_at) VALUES (?, ?, ?)',
                                    (employee_id, description, utc_now()))
                db.commit()
            except sqlite3.IntegrityError:
                return error('У этого сотрудника уже запущен таймер.', 409)
        return jsonify(id=cursor.lastrowid, employee_id=employee_id, description=description), 201

    @app.post('/api/timers/<int:entry_id>/stop')
    def stop_timer(entry_id):
        with connect() as db:
            row = db.execute('SELECT started_at, stopped_at FROM entries WHERE id=?', (entry_id,)).fetchone()
            if not row:
                return error('Запись не найдена.', 404)
            if row['stopped_at'] is not None:
                return error('Таймер уже остановлен.', 409)
            # Compare-and-set protects against an overlapping stop request.
            now = utc_now()
            result = db.execute('UPDATE entries SET stopped_at=? WHERE id=? AND stopped_at IS NULL', (now, entry_id))
            if result.rowcount != 1:
                return error('Таймер уже остановлен.', 409)
        return jsonify(id=entry_id, stopped_at=now)

    @app.get('/api/active')
    def active():
        with connect() as db:
            rows = db.execute('''SELECT e.id, e.employee_id, p.name, e.description, e.started_at
                                FROM entries e JOIN employees p ON p.id = e.employee_id
                                WHERE e.stopped_at IS NULL ORDER BY e.started_at''').fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get('/api/report')
    def report():
        start = request.args.get('from', '')
        end = request.args.get('to', '')
        try:
            start_date, end_date = parse_date(start), parse_date(end)
        except ValueError:
            return error('Укажите даты в формате YYYY-MM-DD.')
        if start_date > end_date:
            return error('Начальная дата не может быть позже конечной.')
        from_dt = datetime.combine(start_date, datetime.min.time(), timezone.utc)
        until_dt = datetime.combine(end_date, datetime.max.time(), timezone.utc)
        now = datetime.now(timezone.utc)
        with connect() as db:
            employees = [dict(r) for r in db.execute('SELECT id, name FROM employees ORDER BY id')]
            entries = [dict(r) for r in db.execute('''SELECT id, employee_id, description, started_at, stopped_at
                FROM entries WHERE started_at <= ? AND (stopped_at IS NULL OR stopped_at >= ?)
                ORDER BY started_at DESC''', (until_dt.isoformat(), from_dt.isoformat()))]
        totals = {e['id']: 0 for e in employees}
        for e in entries:
            started = datetime.fromisoformat(e['started_at'])
            stopped = datetime.fromisoformat(e['stopped_at']) if e['stopped_at'] else now
            overlap_start, overlap_end = max(started, from_dt), min(stopped, until_dt)
            seconds = max(0, (overlap_end - overlap_start).total_seconds())
            e['seconds_in_period'] = round(seconds, 3)
            e['active'] = e['stopped_at'] is None
            totals[e['employee_id']] += seconds
        return jsonify(employees=[dict(e, total_seconds=round(totals[e['id']], 3)) for e in employees],
                       entries=entries, from_date=start, to_date=end)

    return app


if __name__ == '__main__':
    create_app().run(debug=False, port=int(os.environ.get('PORT', 5000)))
