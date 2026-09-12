import datetime as dt
import os
import unittest

os.environ["LLM_FORCE_STUB"] = "1"

from app import mail  # noqa: E402


class Mail(unittest.TestCase):
    def test_clean_strips_private_fields(self):
        raw = {"uid": "7", "subject": "关于提交大学物理实验报告的通知 zhang@sjtu.edu.cn", "date": "Mon, 14 Sep 2026",
               "from_domain": "sjtu.edu.cn",
               "body": "各位同学:\n请于9月20日前提交实验报告,详见 https://x.sjtu.edu.cn/a 联系 13812345678\n> 引用的旧内容\n-----原始邮件-----\nFrom: someone\n签名"}
        c = mail.clean(raw)
        self.assertNotIn("@", c["subject"]); self.assertIn("[邮箱]", c["subject"])
        self.assertNotIn("http", c["excerpt"]); self.assertNotIn("1381", c["excerpt"])
        self.assertNotIn("引用的旧内容", c["excerpt"]); self.assertNotIn("签名", c["excerpt"])
        self.assertIn("9月20日", c["excerpt"])

    def test_extract_stub(self):
        cleaned = [mail.clean({"uid": "1", "subject": "校园歌手大赛海报", "body": "欢迎观看", "date": ""}),
                   mail.clean({"uid": "2", "subject": "线性代数作业3", "body": "请于 9月25日 前提交", "date": ""})]
        tasks = mail.extract_tasks(cleaned, dt.date(2026, 9, 16))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["uid"], "2"); self.assertEqual(tasks[0]["due"], "2026-09-25")


if __name__ == "__main__":
    unittest.main()
