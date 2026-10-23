"""Refresh the repository's sample CSV dates; no application data is changed."""
import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(__file__).resolve().parent.parent / 'static' / 'samples'
root.mkdir(parents=True, exist_ok=True)
today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
with (root / 'sales.csv').open('w', newline='', encoding='utf-8') as file:
    writer = csv.writer(file)
    writer.writerow(['sku', 'warehouse', 'date', 'quantity'])
    for i in range(90, 0, -1):
        writer.writerow(['SKU-1001', 'Mumbai Central', today-timedelta(days=i), 5+i%4])
with (root / 'inventory.csv').open('w', newline='', encoding='utf-8') as file:
    writer = csv.writer(file)
    writer.writerow(['sku', 'warehouse', 'quantity'])
    writer.writerow(['SKU-1001', 'Mumbai Central', 35])
print('Sample inventory and 90-day sales CSV files generated.')

