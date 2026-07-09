from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "tools/BizHawk-2.11.1/PSX/State/Resident Evil - Director's Cut (USA).Nymashock.QuickSave5.State"
data = p.read_bytes()
print(data[:200])
print('---')
# scan for 2MB contiguous region with PS1-like values at known offsets
off_box = 0xC8724
off_inv = 0xC8784
needle_inv = bytes([0x01, 0x01, 0x02, 0x0F])  # knife x1, beretta x15 typical
for i in range(0, len(data)-0xC8800):
    chunk = data[i:i+0xC8800]
    if len(chunk) < off_inv+8:
        continue
    inv = chunk[off_inv:off_inv+8]
    if inv[0] == 0x01 and inv[2] == 0x02:  # knife + beretta pattern
        box = chunk[off_box:off_box+8]
        print(f'hit @ file offset {i}: inv={inv.hex()} box={box.hex()}')
