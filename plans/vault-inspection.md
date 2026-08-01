# Vault Inspection Results

## Commands to run

### 1. Inspect header:
```bash
cd /home/i/Downloads/Pyntara && python3 -c "
from pathlib import Path
v = Path('secrets/default.vault')
print(f'File size: {v.stat().st_size} bytes')
with v.open('rb') as f:
    h = f.read(200)
print(f'First 4 bytes: {h[:4].hex()}')
print(f'Bytes 4-8: {h[4:8].hex()}')
"
```

### 2. Time wrong-password open:
```bash
cd /home/i/Downloads/Pyntara && python3 -c "
import time
from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError
start = time.monotonic()
try:
    kp = PyKeePass('secrets/default.vault', password='wrong')
    print(f'OPENED after {time.monotonic()-start:.2f}s')
except CredentialsError as e:
    print(f'CredentialsError after {time.monotonic()-start:.2f}s: {e}')
except Exception as e:
    print(f'{type(e).__name__} after {time.monotonic()-start:.2f}s: {e}')
"