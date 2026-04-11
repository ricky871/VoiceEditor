import os
import yaml
import argparse
import paramiko
from scp import SCPClient
from pathlib import Path

# Paths to ignore during sync
IGNORE_DIRS = [
    ".git",
    ".venv",
    ".cache",
    ".pytest_cache",
    "__pycache__",
    "checkpoints",
    "work",
    "node_modules",
]
IGNORE_FILES = [
    ".DS_Store",
    "remote_config.yaml", # Don't sync the config itself
]

def load_config(config_path="remote_config.yaml", host_name=None):
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    hosts = config.get("hosts", [])
    if not hosts:
        raise ValueError("No hosts found in config.")
    
    if host_name:
        for h in hosts:
            if h["name"] == host_name:
                return h
        raise ValueError(f"Host '{host_name}' not found in config.")
    
    return hosts[0]

def create_ssh_client(host_info):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    key_path = host_info.get("ssh_key")
    password = host_info.get("password")
    
    if key_path:
        ssh.connect(
            host_info["ip"],
            port=host_info.get("port", 22),
            username=host_info["user"],
            key_filename=key_path,
            password=password, # Fallback if key requires password
            timeout=10
        )
    else:
        ssh.connect(
            host_info["ip"],
            port=host_info.get("port", 22),
            username=host_info["user"],
            password=password,
            timeout=10
        )
    return ssh

def sync_files(host_info):
    local_root = Path.cwd()
    remote_root = host_info["remote_dir"]
    
    print(f"[*] Syncing {local_root} to {host_info['ip']}:{remote_root}...")
    
    ssh = create_ssh_client(host_info)
    # Ensure remote directory exists
    ssh.exec_command(f"mkdir -p {remote_root}")
    
    with SCPClient(ssh.get_transport()) as scp:
        for root, dirs, files in os.walk(local_root):
            # Filter directories
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            
            rel_path = Path(root).relative_to(local_root)
            remote_path = Path(remote_root) / rel_path
            
            # Create remote directory if it's not the root
            if rel_path != Path("."):
                ssh.exec_command(f"mkdir -p '{remote_path.as_posix()}'")
            
            for file in files:
                if file in IGNORE_FILES:
                    continue
                
                local_file = Path(root) / file
                remote_file = remote_path / file
                
                # Simple "modified time" check could be added here, 
                # but SCPClient is generally simple. 
                # For more efficiency, consider sftp.stat() or rsync.
                print(f"  > {rel_path / file}")
                scp.put(str(local_file), remote_file.as_posix())
                
    ssh.close()
    print("[+] Sync complete.")

def execute_command(host_info, cmd):
    print(f"[*] Executing on {host_info['name']}: {cmd}")
    ssh = create_ssh_client(host_info)
    
    # Run command and print output
    stdin, stdout, stderr = ssh.exec_command(f"cd {host_info['remote_dir']} && {cmd}")
    
    for line in stdout:
        print(line.strip())
    for line in stderr:
        print(f"ERR: {line.strip()}")
        
    ssh.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoiceEditor Remote Sync & Debug Tool")
    parser.add_argument("--host", help="Host name from config")
    parser.add_argument("--sync", action="store_true", help="Sync local files to remote")
    parser.add_argument("--exec", dest="command", help="Execute command on remote")
    parser.add_argument("--config", default="remote_config.yaml", help="Path to config file")
    
    args = parser.parse_args()
    
    try:
        host_info = load_config(args.config, args.host)
        
        if args.sync:
            sync_files(host_info)
        
        if args.command:
            execute_command(host_info, args.command)
            
        if not args.sync and not args.command:
            parser.print_help()
            
    except Exception as e:
        print(f"[-] Error: {e}")
