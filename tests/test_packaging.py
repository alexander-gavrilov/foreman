import json
import os
import re

from tests.helpers import ROOT, ForemanTestCase  # noqa: F401


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Packaging(ForemanTestCase):
    def test_skill_frontmatter_and_budget(self):
        for rel, limit in ((("skills", "foreman", "SKILL.md"), 120),
                           (("skills", "foreman-orchestrator", "SKILL.md"), 150)):
            text = read(*rel)
            m = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", text)  # shape; strictness: next test
            self.assertTrue(m, rel)
            self.assertEqual(m.group(1), rel[1])
            self.assertLessEqual(len(text.splitlines()), limit)

    def test_every_skill_frontmatter_is_strict_yaml(self):
        """`npx skills add` skips a SKILL.md whose frontmatter is invalid YAML (an unquoted ': ' in a plain
        scalar was the bug). Check the YAML subset we use: `key: value` lines, plain scalars free of ': ' and
        ' #', quoted scalars properly closed; name and description are required."""
        found = 0
        skills_dir = os.path.join(ROOT, "skills")
        for name in sorted(os.listdir(skills_dir)):
            path = os.path.join(skills_dir, name, "SKILL.md")
            if not os.path.exists(path):
                continue
            found += 1
            lines = read("skills", name, "SKILL.md").split("\n")
            self.assertEqual(lines[0], "---", name)
            end = lines.index("---", 1)
            keys = {}
            for line in lines[1:end]:
                m = re.match(r"^([A-Za-z][\w-]*): (.*)$", line)
                self.assertTrue(m, "%s: not a `key: value` line: %r" % (name, line))
                key, value = m.groups()
                if value.startswith('"'):
                    keys[key] = json.loads(value)  # JSON double-quoted scalars are valid YAML
                elif value.startswith("'"):
                    self.assertRegex(value, r"^'(?:[^']|'')*'$", "%s.%s" % (name, key))
                    keys[key] = value
                else:
                    self.assertNotIn(": ", value, "%s.%s: unquoted ': ' in a plain scalar" % (name, key))
                    self.assertNotIn(" #", value, "%s.%s: ' #' starts a comment" % (name, key))
                    self.assertNotRegex(value, r"^[\[\]{}&*!|>%@`,?-]", "%s.%s: bad first character" % (name, key))
                    keys[key] = value
            self.assertIn("name", keys, name)
            self.assertIn("description", keys, name)
            self.assertEqual(keys["name"], name)
        self.assertGreaterEqual(found, 2)

    def test_install_phrase_in_skill(self):
        text = read("skills", "foreman", "SKILL.md")
        self.assertIn("npx skills add alexander-gavrilov/foreman -g -y", text)
        self.assertIn("~/.agents/skills/foreman/bin/foreman install", text)
        self.assertIn("foreman init", text)

    def test_plugin_manifests(self):
        plugin = json.loads(read(".claude-plugin", "plugin.json"))
        self.assertEqual(plugin["name"], "foreman")
        market = json.loads(read(".claude-plugin", "marketplace.json"))
        self.assertEqual(market["plugins"][0]["name"], "foreman")
        self.assertTrue(os.path.isdir(os.path.join(ROOT, "skills", "foreman")))

    def test_readme_lists_every_wp7_command(self):
        text = read("README.md")
        for cmd in ("foreman init", "foreman install", "foreman report", "foreman uninstall"):
            self.assertIn(cmd, text)

    def test_project_rule_template_exists_and_fits_budget(self):
        text = read("skills", "foreman", "templates", "project-rule.md").strip()
        lines = text.splitlines()
        self.assertGreaterEqual(len(lines), 4)
        self.assertLessEqual(len(lines), 10)
        for part in ("foreman work new", "other agents", "foreman report", "foreman stage"):
            self.assertIn(part, text)
        # F5: a rule finding must never become a public issue by accident
        self.assertIn("foreman report --project", text)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "skills", "foreman", "templates", "rules-block.md")))


class OrchestratorJournalForm(ForemanTestCase):
    """The orchestrator is told the exact journal command, and the guard lets exactly that form through."""
    FORM = 'foreman log append "'

    def test_briefing_and_skill_show_the_exact_form(self):
        for rel in (("skills", "foreman", "templates", "orchestrator-briefing.md"),
                    ("skills", "foreman-orchestrator", "SKILL.md")):
            self.assertIn(self.FORM, read(*rel), rel)

    def test_the_guard_allows_it(self):
        from foreman import guard
        for cmd in ('foreman log append "decided: merge after review"', "foreman log append 'one line' --kind note"):
            self.assertIsNone(guard.check_command(cmd), cmd)
