import calendar
import re
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path


def parse_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('날짜는 YYYY-MM-DD 형식으로 입력해 주세요.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError('존재하는 날짜를 입력해 주세요.') from None


def validate(day, item, amount):
    day = parse_date(day).isoformat()
    item = item.strip()
    if not 1 <= len(item) <= 100:
        raise ValueError('항목은 1~100자로 입력해 주세요.')
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 10**12:
        raise ValueError('금액은 0~1조 원 사이의 정수로 입력해 주세요.')
    return day, item, amount


def week_bounds(day):
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def month_bounds(day):
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                day TEXT NOT NULL, item TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK(amount >= 0),
                success INTEGER NOT NULL CHECK(success IN (0,1)),
                version INTEGER NOT NULL DEFAULT 1)''')
            db.execute('CREATE INDEX IF NOT EXISTS scope_day ON entries(guild_id,user_id,day)')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def save(self, guild, user, day, item, amount, success, entry_id=None, version=None):
        day, item, amount = validate(day, item, amount)
        if type(success) is not bool:
            raise ValueError('빵원 성공 여부를 선택해 주세요.')
        with closing(self.connect()) as db, db:
            if entry_id is None:
                cur = db.execute('INSERT INTO entries(guild_id,user_id,day,item,amount,success) VALUES(?,?,?,?,?,?)',
                                 (guild, user, day, item, amount, int(success)))
                return cur.lastrowid
            cur = db.execute('''UPDATE entries SET day=?,item=?,amount=?,success=?,version=version+1
                WHERE id=? AND guild_id=? AND user_id=? AND version=?''',
                (day, item, amount, int(success), entry_id, guild, user, version))
            if cur.rowcount != 1:
                raise ValueError('기록이 수정되었거나 삭제되었습니다. 명령을 다시 실행해 주세요.')
            return entry_id

    def get(self, guild, user, entry_id):
        with closing(self.connect()) as db:
            return db.execute('SELECT * FROM entries WHERE guild_id=? AND user_id=? AND id=?',
                              (guild, user, entry_id)).fetchone()

    def delete(self, guild, user, entry_id, version):
        with closing(self.connect()) as db, db:
            cur = db.execute('DELETE FROM entries WHERE guild_id=? AND user_id=? AND id=? AND version=?',
                             (guild, user, entry_id, version))
            if cur.rowcount != 1:
                raise ValueError('기록이 변경되었거나 삭제되었습니다. 다시 확인해 주세요.')

    def rows(self, guild, user, start, end, page=1):
        with closing(self.connect()) as db:
            args = (guild, user, str(start), str(end))
            count = db.execute('SELECT COUNT(*) FROM entries WHERE guild_id=? AND user_id=? AND day BETWEEN ? AND ?', args).fetchone()[0]
            rows = db.execute('SELECT * FROM entries WHERE guild_id=? AND user_id=? AND day BETWEEN ? AND ? ORDER BY day DESC,id DESC LIMIT 10 OFFSET ?', args + ((page-1)*10,)).fetchall()
            return rows, count

    def summary(self, guild, user, start, end):
        with closing(self.connect()) as db:
            # Python integers avoid SQLite SUM overflow for large accumulated totals.
            rows = db.execute('SELECT day,amount,success FROM entries WHERE guild_id=? AND user_id=? AND day BETWEEN ? AND ?',
                              (guild, user, str(start), str(end))).fetchall()
            return sum(r['amount'] for r in rows), len({r['day'] for r in rows if r['success']})
