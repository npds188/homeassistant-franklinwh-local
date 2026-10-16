"""The third-party licence notice must ship inside the integration folder (HACS and manual
installs copy only custom_components/franklinwh_local) and match the repo-root copy.

Run:  python tests/test_notices.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(ROOT, "custom_components", "franklinwh_local")
results = []


def check(name, ok):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)


pkg_notice = os.path.join(PKG, "THIRD_PARTY_NOTICES.md")
root_notice = os.path.join(ROOT, "THIRD_PARTY_NOTICES.md")
check("notice ships inside the integration folder", os.path.isfile(pkg_notice))
text = open(pkg_notice).read() if os.path.isfile(pkg_notice) else ""
check("contains david2069's MIT copyright line", "Copyright (c) 2026 david2069" in text)
check("contains the MIT permission notice", "Permission is hereby granted, free of charge" in text)
check("contains the MIT warranty disclaimer", 'THE SOFTWARE IS PROVIDED "AS IS"' in text)
check("root copy is identical", os.path.isfile(root_notice) and open(root_notice).read() == text)
client = open(os.path.join(PKG, "client.py")).read()
check("adapted code points to the in-folder notice", client.count("THIRD_PARTY_NOTICES.md in this folder") == 2)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
