#!/usr/bin/env python3
"""
飞书消息发送脚本
使用方法:
    python feishu_send.py --email junjie@graceim.ai --text "你好"
    python feishu_send.py --email junjie@graceim.ai --card --title "标题" --content "内容"
"""

import requests
import json
import argparse
import sys

from ops.utils.logger.log import info, warn, error

class FeishuBot:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = "https://open.feishu.cn/open-apis"
        self.token = self._get_token()
    
    def _get_token(self):
        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        response = requests.post(url, json=payload)
        result = response.json()
        
        if result.get("code") == 0:
            return result.get("tenant_access_token")
        else:
            raise Exception(f"Token 获取失败: {result}")
    
    def get_open_id_by_email(self, email):
        url = f"{self.base_url}/contact/v3/users"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        params = {"user_id_type": "open_id", "page_size": 50}
        
        response = requests.get(url, headers=headers, params=params)
        result = response.json()
        
        if result.get("code") == 0:
            items = result.get("data", {}).get("items", [])
            for user in items:
                user_email = user.get("email") or user.get("enterprise_email")
                if user_email == email:
                    return user.get("open_id")
        
        return None
    
    def send_text_message(self, open_id, text):
        url = f"{self.base_url}/im/v1/messages"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        params = {"receive_id_type": "open_id"}
        
        payload = {
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text})
        }
        
        response = requests.post(url, headers=headers, params=params, json=payload)
        result = response.json()
        
        if result.get("code") == 0:
            return True
        else:
            raise Exception(f"消息发送失败: {result.get('msg')}")
    
    def send_card_message(self, open_id, title, content):
        url = f"{self.base_url}/im/v1/messages"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        params = {"receive_id_type": "open_id"}
        
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": content
                    }
                }
            ]
        }
        
        payload = {
            "receive_id": open_id,
            "msg_type": "interactive",
            "content": json.dumps(card)
        }
        
        response = requests.post(url, headers=headers, params=params, json=payload)
        result = response.json()
        
        if result.get("code") == 0:
            return True
        else:
            raise Exception(f"卡片发送失败: {result.get('msg')}")


def main():
    # 配置信息（可以改成从配置文件读取）
    APP_ID = "cli_a9bd283a8eb89bd8"
    APP_SECRET = "F6tkhCKkg9ai0q7Z2kczNc6ex5HGgqqH"  # 替换成你的
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='飞书消息发送工具')
    parser.add_argument('--email', required=True, help='接收者邮箱')
    parser.add_argument('--text', help='文本消息内容')
    parser.add_argument('--card', action='store_true', help='发送卡片消息')
    parser.add_argument('--title', help='卡片标题')
    parser.add_argument('--content', help='卡片内容')
    
    args = parser.parse_args()
    
    # 创建机器人
    try:
        bot = FeishuBot(APP_ID, APP_SECRET)

        # 获取 open_id
        info(f"正在查找用户: {args.email}")
        open_id = bot.get_open_id_by_email(args.email)

        if not open_id:
            error(f"❌ 未找到用户: {args.email}")
            sys.exit(1)

        info(f"✅ 找到用户: {open_id}")

        # 发送消息
        if args.card:
            if not args.title or not args.content:
                error("❌ 发送卡片需要 --title 和 --content 参数")
                sys.exit(1)

            info("正在发送卡片消息...")
            bot.send_card_message(open_id, args.title, args.content)
            info("✅ 卡片消息发送成功!")

        elif args.text:
            info("正在发送文本消息...")
            bot.send_text_message(open_id, args.text)
            info("✅ 文本消息发送成功!")

        else:
            error("❌ 请指定 --text 或 --card")
            sys.exit(1)

    except Exception as e:
        error(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
