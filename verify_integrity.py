"""Verify all versioned release file digests against SHA256SUMS."""
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def main():
    n=0
    for line in (ROOT/'SHA256SUMS').read_text().splitlines():
        expected,name=line.split('  ',1)
        actual=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        if actual!=expected:raise ValueError(f'Digest mismatch: {name}')
        n+=1
    print(f'All {n} release files match SHA-256.')

if __name__=='__main__':main()
