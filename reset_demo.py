from pathlib import Path

root = Path(__file__).resolve().parent
DB = root / 'data' / 'resq.db'
if DB.exists():
    DB.unlink()
    print('Demo database reset.')
else:
    print('No demo database existed; a new one will be created on the next start.')

print('Start RESQ again with: python app.py')
