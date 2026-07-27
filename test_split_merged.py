#!/usr/bin/env python3
"""Round-trip check: split_merged() must undo get_merged_content()."""

from vicron.repo import module_separator, split_merged


def test_round_trip():
    modules = {
        "main": "MAILTO=me\n0 3 * * 7 backup.sh\n",
        "dba": "# vicron module: dba\n0 5 * * 1-5 check.py\n",
        "gdh": "# vicron module: gdh\n59 15 * * 1 gdh in\n",
    }
    merged = modules["main"]
    for name in ("dba", "gdh"):
        merged += module_separator(name) + modules[name]

    assert split_merged(merged) == modules
    # no separators at all -> everything is the main module
    assert split_merged("0 1 * * * x\n") == {"main": "0 1 * * * x\n"}


if __name__ == "__main__":
    test_round_trip()
    print("ok")
