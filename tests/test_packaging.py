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
            m = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", text)
            self.assertTrue(m, rel)
            self.assertEqual(m.group(1), rel[1])
            self.assertLessEqual(len(text.splitlines()), limit)

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
