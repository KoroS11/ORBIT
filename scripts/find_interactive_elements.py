import os
import re
from pathlib import Path

ROOT = Path("src")
PATTERNS = [
    r'onClick\s*=\s*\{([^}]+)\}',
    r'onChange\s*=\s*\{([^}]+)\}',
    r'onSubmit\s*=\s*\{([^}]+)\}',
    r'onKeyDown\s*=\s*\{([^}]+)\}',
    r'<button\b',
    r'<select\b',
    r'<input\b',
    r'<a\b',
]

def scan_files():
    results = []
    for p in sorted(ROOT.rglob("*.tsx")):
        lines = p.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, 1):
            for pat in [r'onClick', r'onChange', r'onSubmit', r'onKeyDown', r'<button', r'<select', r'<input', r'<a\s+href']:
                if re.search(pat, line):
                    results.append((p.as_posix(), idx, line.strip()))
                    break
    return results

if __name__ == "__main__":
    elements = scan_files()
    print(f"Total interactive matches: {len(elements)}")
    for file, line, content in elements[:50]:
        print(f"{file}:{line} -> {content}")
