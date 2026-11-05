"""Core of the stub `orca` / `herdr` executables used by transport tests.

Every call appends {"tool", "argv"} to $STUB_DIR/calls.jsonl and answers with the first rule in
$STUB_DIR/rules.json whose "match" is a prefix of argv. A rule is
{"match": [...], "json": <obj> | "stdout": "<text>", "stderr": "<text>", "code": <int>}.
No rule -> exit 99. Nothing here ever reaches a real orca or herdr."""
import json
import os
import sys


def main(tool):
    base = os.environ["STUB_DIR"]
    argv = sys.argv[1:]
    with open(os.path.join(base, "calls.jsonl"), "a") as fh:
        fh.write(json.dumps({"tool": tool, "argv": argv}) + "\n")
    try:
        with open(os.path.join(base, "rules.json")) as fh:
            rules = json.load(fh)
    except OSError:
        rules = []
    for rule in rules:
        match = rule["match"]
        if argv[:len(match)] == match:
            if "json" in rule:
                sys.stdout.write(json.dumps(rule["json"]))
            elif "stdout" in rule:
                sys.stdout.write(rule["stdout"])
            sys.stderr.write(rule.get("stderr", ""))
            return int(rule.get("code", 0))
    sys.stderr.write("stub: no rule for %s %s\n" % (tool, " ".join(argv)))
    return 99
