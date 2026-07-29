from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "fa-server-operations"


class FaServerOperationsSkillTests(unittest.TestCase):
    def test_skill_frontmatter_and_resources_are_complete(self):
        skill_path = SKILL_ROOT / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        lines = content.splitlines()

        self.assertEqual("---", lines[0])
        frontmatter_end = lines.index("---", 1)
        frontmatter = {}
        for line in lines[1:frontmatter_end]:
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()

        self.assertEqual("fa-server-operations", frontmatter["name"])
        self.assertIn("FA Server", frontmatter["description"])
        self.assertNotIn("TODO", content)
        self.assertTrue(
            (SKILL_ROOT / "references" / "api-workflows.md").is_file()
        )

    def test_agent_metadata_declares_mcp_dependency(self):
        metadata = (
            SKILL_ROOT / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn('display_name: "FA Server Operations"', metadata)
        self.assertIn('value: "fa-server"', metadata)
        self.assertIn("$fa-server-operations", metadata)


if __name__ == "__main__":
    unittest.main()
