import argparse
import os
from pathlib import Path
import paramiko
import yaml

class RemoteDebug:
    def __init__(self, config_path="configs/remote_hosts.yaml"):
        self.config_path = Path(config_path)
        with open(self.config_path, "r") as f:
            self.config = yaml.safe_load(f)
        self.host_info = self.config["hosts"][0]
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self):
        pkey = None
        if "ssh_key" in self.host_info:
            ssh_key_path = Path(self.host_info["ssh_key"])
            if ssh_key_path.exists():
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(str(ssh_key_path))
                except:
                    pkey = paramiko.RSAKey.from_private_key_file(str(ssh_key_path))
        self.client.connect(
            hostname=self.host_info["ip"],
            port=self.host_info.get("port", 22),
            username=self.host_info["user"],
            pkey=pkey,
            password=self.host_info.get("password"),
            timeout=30
        )

    def run_command(self, cmd, use_sudo=False):
        if use_sudo:
            # Use echo to pipe the password into sudo -S
            password = self.host_info.get("password", "")
            cmd = f"echo '{password}' | sudo -S {cmd}"
        
        print(f"\n--- Executing: {cmd.replace(self.host_info.get('password', 'SKIP'), '********')} ---")
        stdin, stdout, stderr = self.client.exec_command(cmd)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        if out: print(f"STDOUT:\n{out}")
        if err: print(f"STDERR:\n{err}")
        return out, err

    def debug(self, custom_cmd=None):
        self.connect()
        if custom_cmd:
            # Handle potential sudo in custom commands
            if "sudo" in custom_cmd:
                password = self.host_info.get("password", "")
                custom_cmd = custom_cmd.replace("sudo ", f"echo '{password}' | sudo -S ")
            self.run_command(custom_cmd)
            self.client.close()
            return

        # 1. Check if service is running
        self.run_command("systemctl status voiceeditor.service --no-pager")
        
        # 2. Check listening ports
        self.run_command("ss -tlnp | grep 8196")
        
        # 3. Check firewall (ufw)
        self.run_command("ufw status", use_sudo=True)
        
        # 4. Try to open port 8196 in UFW
        print("\n--- Attempting to open port 8196 in UFW ---")
        self.run_command("ufw allow 8196/tcp", use_sudo=True)
        self.run_command("ufw status", use_sudo=True)
        
        # 5. Try local curl to confirm the server is working internally
        self.run_command("curl -I http://127.0.0.1:8196")
        
        self.client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cmd", type=str, help="Custom command to run")
    args = parser.parse_args()
    RemoteDebug().debug(args.cmd)
