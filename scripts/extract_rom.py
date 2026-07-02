"""One-shot: extract the disc image archive into roms/."""
from pathlib import Path

import py7zr

archive = Path(r"D:\re1_rl\roms\Resident Evil - Director's Cut.7z")
dest = archive.parent

with py7zr.SevenZipFile(archive) as a:
    for f in a.list():
        print(f.filename, f.uncompressed)
    a.extractall(dest)
print("EXTRACT_DONE")
