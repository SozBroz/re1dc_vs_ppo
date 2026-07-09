import zipfile
from pathlib import Path
p = list(Path("tools/BizHawk-2.11.1/PSX/State").glob("QuickSave5.State"))
# glob won't work - use sorted
states = sorted(Path("tools/BizHawk-2.11.1/PSX/State").glob("*.QuickSave5.State"))
p = states[0]
z = zipfile.ZipFile(p)
print(z.read("BizVersion.txt"))
data = z.read("BizState 1.0")
print("BizState len", len(data))
print(data[:800])
