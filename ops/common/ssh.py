import os
import sys
import paramiko


def init_ssh_client(host: str, port: int, password: str = '', key_path: str = "~/.ssh/id_rsa") -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key_path = os.path.expanduser(key_path)

    try:
        # 优先密钥认证
        if os.path.exists(key_path):
            private_key = paramiko.RSAKey.from_private_key_file(key_path)
            ssh.connect(
                hostname=host,
                port=port,
                username='wbai',
                pkey=private_key,
                timeout=15
            )
            print(f"✅ SSH密钥认证成功: wbai@{host}:{port}")
        # 密码认证
        elif password:
            ssh.connect(
                hostname=host,
                port=port,
                username='wbai',
                password=password,
                timeout=15
            )
            print(f"✅ SSH密码认证成功: wbai@{host}:{port}")
        else:
            raise Exception("未提供有效认证方式（密钥文件不存在且无密码）")
        return ssh
    except Exception as e:
        transport = ssh.get_transport()
        if transport and transport.is_active():
            ssh.close()
        sys.exit(f"❌ SSH连接失败: {str(e)}")

