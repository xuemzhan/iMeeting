"""Read-only field-log audit. Run from any directory; writes only this evidence folder."""
import collections
import hashlib
import json
import pathlib
import re
import sqlite3

OUT = pathlib.Path(__file__).resolve().parent
ROOT = OUT.parents[3]
LOG = ROOT / 'backend/data/logs/backend.log'
raw = LOG.read_bytes()
lines = raw.decode('utf-8').splitlines()
result = {'source': str(LOG), 'sha256': hashlib.sha256(raw).hexdigest(),
          'bytes': len(raw), 'lines': len(lines), 'days': {}, 'sessions': []}
for day in ('2026-10-31', '2026-11-01'):
    rows = [(i + 1, line) for i, line in enumerate(lines) if line.startswith(day)]
    failures = []
    errors = []
    for number, line in rows:
        if '无法解析为 JSON' in line:
            preceding = next((lines[j] for j in range(number - 2, max(-1, number - 25), -1)
                              if 'llm_call ' in lines[j]), '')
            failures.append({'line': number, 'event': line, 'preceding_call': preceding})
        if ' ERROR ' in line:
            errors.append({'line': number, 'event': line,
                           'exception': [x for x in lines[number:number + 90]
                                         if re.match(r'(?:sqlite3\.|sqlalchemy\.exc\.|RuntimeError:|OSError:|httpx\.)', x)]})
    result['days'][day] = {
        'timestamped_lines': len(rows), 'first': rows[0][1][:23], 'last': rows[-1][1][:23],
        'parse_failures': failures, 'errors': errors,
        'offline_empty_warnings': sum('离线模型返回空文本' in x for _, x in rows),
        'hotword_list_lines': sum('Hotword list:' in x for _, x in rows)}
    for number, line in rows:
        if 'meeting_metrics {' in line:
            metric = json.loads(line.split('meeting_metrics ', 1)[1])
            if metric['duration_ms'] > 60000 and metric['mode'] == 'normal':
                result['sessions'].append({'log_line': number, **metric})
db = ROOT / 'backend/data/meeting.db'
with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
    conn.execute('BEGIN')
    result['meetings'] = conn.execute('SELECT id,status,duration_ms,segment_count FROM meeting').fetchall()
    result['minutes'] = conn.execute('SELECT meeting_id,status,is_draft,count(*),max(transcript_end_ms) FROM minutes GROUP BY meeting_id,status,is_draft').fetchall()
    result['sessions_12'] = conn.execute('SELECT session_id,start_offset_ms,duration_ms,status FROM meeting_session WHERE meeting_id=12').fetchall()
    result['speaker_sources_13_current'] = conn.execute('SELECT speaker_source,count(*) FROM segment WHERE meeting_id=13 GROUP BY speaker_source').fetchall()
    conn.rollback()
(OUT / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
for day, data in result['days'].items():
    print(day, data['last'], 'parse failures:', len(data['parse_failures']),
          'empty warnings:', data['offline_empty_warnings'], 'errors:', len(data['errors']))
for s in result['sessions']:
    print(s['meeting_id'], s['session_id'], s['segments'], s['final_latency_ms'])
