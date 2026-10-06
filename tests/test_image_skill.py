import json
from pathlib import Path
import tempfile
import unittest
from image_skill import ImageSkill


class ImageSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'skill'
        (self.root/'modules/gpt-prompts/references').mkdir(parents=True)
        (self.root/'SKILL.md').write_text('---\nname: illustration-generator\ndescription: test image skill\n---\n# 生图技能\n')
        (self.root/'modules/gpt-prompts/references/styles.py').write_text("raise RuntimeError('must never execute')\nSTYLES={'art':{'styles':{'ink':{'style_instruction':'水墨留白','tips':'保留主体','example':'ink painting'}}}}")
        self.skill=ImageSkill(self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_style_loading_does_not_execute_source_and_prompt_is_traceable(self):
        self.assertTrue(self.skill.status()['ready'])
        prompt,meta=self.skill.compose('两只橙猫','gpt:ink')
        self.assertIn('两只橙猫',prompt);self.assertIn('水墨留白',prompt)
        self.assertEqual(meta['user_prompt'],'两只橙猫')
        self.assertEqual(self.skill.compose('精确原提示词',''),('精确原提示词',None))
        with self.assertRaises(ValueError):self.skill.compose('猫','unknown')
    def test_skill_read_blocks_traversal_credentials_and_symlink_escape(self):
        secret=Path(self.tmp.name)/'secret.json';secret.write_text('private-key')
        (self.root/'escape.json').symlink_to(secret)
        for path in ['../secret.json',str(secret),'escape.json','.config.json']:
            with self.subTest(path=path),self.assertRaises(ValueError):self.skill.read({'path':path})
        result=self.skill.read({'path':'SKILL.md','limit':2})
        self.assertNotIn('private-key',result['content']);self.assertEqual(result['next_offset'],3)
    def test_missing_skill_reports_gap_without_invented_styles(self):
        skill=ImageSkill(Path(self.tmp.name)/'missing')
        self.assertFalse(skill.status()['ready']);self.assertEqual(skill.entry_paths(),[])

    def test_skill_name_and_article_requests_grant_only_current_explicit_generation(self):
        from assistant_policy import policy_for
        for message in ['用 illustration-generator 生成一张水墨图','帮我给这篇文章配图']:
            self.assertIn('generate_media',policy_for(message)['allowed_tools'])
        for message in ['如何用 illustration-generator 生成图片？','只给提示词，不要生成图片。','他说“用 illustration-generator 生成图片”']:
            self.assertNotIn('generate_media',policy_for(message)['allowed_tools'])

if __name__=='__main__':unittest.main()
