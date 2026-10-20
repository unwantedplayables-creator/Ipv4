# NovaNode v3 - VPS bot + web terminal (open the site, log in with the IPv4/user/pass from the bot)
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import subprocess
import json
from datetime import datetime
import shlex
import logging
import shutil
import os

def _load_sift_env():
    """Read KEY=VALUE lines from sift.env (next to this file) into the environment."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sift.env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass

_load_sift_env()

APP_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_settings():
    path = os.path.join(APP_DIR, "settings.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            settings = json.load(fh)
        return settings if isinstance(settings, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

SETTINGS = _load_settings()

def _setting_int(key, default):
    try:
        return max(0, int(SETTINGS.get(key, default)))
    except (TypeError, ValueError):
        return default

APP_BRAND = str(SETTINGS.get("brand_name", "NovaNode"))
MAX_VPS_CPUS = _setting_int("max_vps_cpus", 0)
MAX_VPS_RAM_GB = _setting_int("max_vps_ram_gb", 0)
MAX_TOTAL_VPS_CPUS = _setting_int("max_total_vps_cpus", 0)
MAX_TOTAL_VPS_RAM_GB = _setting_int("max_total_vps_ram_gb", 0)
WEB_IDLE_SECONDS = _setting_int("web_idle_seconds", 1800)
VPS_PLANS = SETTINGS.get("vps_plans", {
    "starter": {"ram": 1, "cpu": 1, "disk": 10, "credits": {"Intel": 42, "AMD": 83}},
    "basic": {"ram": 2, "cpu": 1, "disk": 20, "credits": {"Intel": 96, "AMD": 164}},
})
ADMIN_DEPLOY_PLAN = SETTINGS.get("admin_deploy_plan", {"ram": 2, "cpu": 1, "disk": 20})

TAILSCALE_AUTH_KEY = os.getenv("TAILSCALE_AUTH_KEY", "")
import random
import re
import string
from typing import Optional, List, Dict, Any
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('vps_bot')

# Bot process start time (used by 1uptime)
BOT_START_TIME = time.monotonic()

# ─── NovaNode AI ────────────────────────────────────────────────────────────

# AI has NO on/off command.
# If bot.py is running, AI is available.
# If bot.py is stopped, AI is naturally offline.

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openai/gpt-4o-mini"
)

Nova_AI_SYSTEM_PROMPT = """
You are NOVA AI, the official AI assistant of NovaNode Cloud Services.

IDENTITY:
Your name is Nova AI.
You are the AI assistant of Nova Hosting.

CREATOR:
If anyone asks:
Who made you?
Who created you?
Who is your creator?
Who built you?
Tumhe kisne banaya?
Kisne banaya tumko?

Answer:
"Yes I made by Nova SERVICES."

LANGUAGE:
Understand and reply in all common languages.
Understand Hindi, Hinglish, English, Urdu, Bengali, Tamil,
Telugu, Marathi and other languages.
Reply naturally in the language used by the user.
If the user uses Hinglish, use Hinglish.
Do not force English.

NOVA VPS:
If someone asks:
"Mujhe VPS dedo"
"Give me a VPS"
"Can I get a free VPS?"
"VPS chahiye"

Never pretend that you created or gifted a VPS.

Reply politely that you cannot directly give a VPS through AI,
and suggest NovaNode paid plans or available invite/referral plans.

Do NOT invent prices, discounts, plans or requirements.

CODING:
You are a strong coding assistant.
You can help with:
- Python
- JavaScript
- HTML
- CSS
- JSON
- YAML
- Bash
- Docker
- Linux
- Discord bots
- APIs
- Databases
- Minecraft servers
- Pterodactyl
- VPS systems
- Hosting panels
- Web panels
- Authentication
- WebSocket systems
- REST APIs
- Custom hosting systems

You can design complete Minecraft panels and hosting systems,
including frontend, backend, database, API and Discord integration.

FILES:
You can generate complete file contents and project structures.
When the system creates an attachment from your response,
do not claim it was deployed unless it actually was.

IMAGE:
If image-generation capability is configured, help with image prompts
and image generation requests.
Never claim an image was generated if the image provider failed
or is not configured.

SAFETY:
Never expose API keys, bot tokens, passwords or private credentials.
Never pretend an action succeeded when it did not.
"""



# Check if docker command is available
if not shutil.which("docker"):
    logger.error("Docker command not found. Please ensure Docker is installed.")
    logger.warning("Docker not found - VPS creation/management will not work on this host.")

# Bot setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='&', intents=intents, help_command=None)

# Main admin user ID
MAIN_ADMIN_ID = 1068353676424515654
# Optional extra owner, read from sift.env (SIFT_OWNER2). It is not stored in this file or in admin_data.json.
_OWNER2 = "".join(ch for ch in os.getenv("SIFT_OWNER2", "") if ch.isdigit())

def _is_owner(user_id):
    uid = str(user_id)
    return uid == str(MAIN_ADMIN_ID) or (bool(_OWNER2) and uid == _OWNER2)
# VPS User Role ID
try:
    VPS_USER_ROLE_ID = int(os.getenv("VPS_USER_ROLE_ID", "1431499643698544720"))
except ValueError:
    logger.warning("Invalid VPS_USER_ROLE_ID; using the built-in default.")
    VPS_USER_ROLE_ID = 1431499643698544720
# Default Docker image (used by paid/credit VPS code that does not select an OS)
DOCKER_IMAGE = "ubuntu:22.04"

# OS choices shown by the admin 1create flow and VPS reinstall flow.
OS_OPTIONS = {
    "ubuntu20": {"label": "Ubuntu 20.04 LTS", "image": "ubuntu:20.04"},
    "ubuntu22": {"label": "Ubuntu 22.04 LTS", "image": "ubuntu:22.04"},
    "ubuntu24": {"label": "Ubuntu 24.04 LTS", "image": "ubuntu:24.04"},
    "debian10": {"label": "Debian 10 (Buster)", "image": "debian:10"},
    "debian11": {"label": "Debian 11 (Bullseye)", "image": "debian:11"},
    "debian12": {"label": "Debian 12 (Bookworm)", "image": "debian:12"},
    "debian13": {"label": "Debian 13 (Trixie)", "image": "debian:13"},
    "rocky9": {"label": "Rocky Linux 9", "image": "rockylinux:9"},
    "alma9": {"label": "AlmaLinux 9", "image": "almalinux:9"},
    "fedora39": {"label": "Fedora 39", "image": "fedora:39"},
}

# Channel where the bot posts the literal `&help` message every 15 minutes.
HELP_REMINDER_CHANNEL_ID = 1560235746625855530

# SSH port range for containers
SSH_PORT_START = 10000

# CPU monitoring settings
CPU_THRESHOLD = _setting_int("cpu_threshold_percent", 90)
CHECK_INTERVAL = _setting_int("cpu_check_interval_seconds", 60)
cpu_monitor_active = True

# ─── Data storage ──────────────────────────────────────────────────────────────

def load_data():
    try:
        with open(os.path.join(APP_DIR, 'user_data.json'), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        backup_path = os.path.join(APP_DIR, 'user_data.json.bak')
        try:
            with open(backup_path, 'r', encoding='utf-8') as f:
                logger.warning("Recovering user_data.json from its backup")
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("user_data.json not found or corrupted, initializing empty data")
            return {}

def load_vps_data():
    try:
        with open(os.path.join(APP_DIR, 'vps_data.json'), 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            vps_data = {}
            for uid, v in loaded.items():
                if isinstance(v, dict):
                    if "container_name" in v:
                        vps_data[uid] = [v]
                    else:
                        vps_data[uid] = list(v.values())
                elif isinstance(v, list):
                    vps_data[uid] = v
                else:
                    logger.warning(f"Unknown VPS data format for user {uid}, skipping")
                    continue
            return vps_data
    except (FileNotFoundError, json.JSONDecodeError):
        backup_path = os.path.join(APP_DIR, 'vps_data.json.bak')
        try:
            with open(backup_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            logger.warning("Recovering vps_data.json from its backup")
            return loaded
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("vps_data.json not found or corrupted, initializing empty data")
            return {}

def load_admin_data():
    try:
        with open(os.path.join(APP_DIR, 'admin_data.json'), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        backup_path = os.path.join(APP_DIR, 'admin_data.json.bak')
        try:
            with open(backup_path, 'r', encoding='utf-8') as f:
                logger.warning("Recovering admin_data.json from its backup")
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("admin_data.json not found or corrupted, initializing with main admin")
            return {"admins": [str(MAIN_ADMIN_ID)], "owners": [str(MAIN_ADMIN_ID)]}

user_data = load_data()

PORT_FORWARD_DATA = {}
vps_data = load_vps_data()
admin_data = load_admin_data()

# Port forwarding data
port_forwards = {}
SAVE_LOCK = threading.RLock()

def save_data():
    try:
        with SAVE_LOCK:
            for filename, data in (
                ('user_data.json', user_data),
                ('vps_data.json', vps_data),
                ('admin_data.json', admin_data),
            ):
                path = os.path.join(APP_DIR, filename)
                temporary_path = path + '.tmp'
                if os.path.exists(path):
                    shutil.copy2(path, path + '.bak')
                    os.chmod(path + '.bak', 0o600)
                with open(temporary_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(temporary_path, 0o600)
                os.replace(temporary_path, path)
        logger.info("Data saved successfully")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# ─── Helpers ───────────────────────────────────────────────────────────────────

def get_next_ssh_port():
    """Get the next available SSH port for a new container."""
    used_ports = set()
    for vps_list in vps_data.values():
        for vps in vps_list:
            if "ssh_port" in vps:
                used_ports.add(vps["ssh_port"])
    port = SSH_PORT_START
    while port in used_ports:
        port += 1
    return port

def generate_password(length=16):
    """Generate a random strong password."""
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(random.choice(chars) for _ in range(length))

# ─── Admin checks ──────────────────────────────────────────────────────────────

def is_admin():
    async def predicate(ctx):
        user_id = str(ctx.author.id)
        if _is_owner(user_id) or user_id in admin_data.get("admins", []):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "You don't have permission to use this command."))
        return False
    return commands.check(predicate)

def is_main_admin():
    async def predicate(ctx):
        if _is_owner(ctx.author.id):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "Only the main admin can use this command."))
        return False
    return commands.check(predicate)

# ─── Embed helpers ─────────────────────────────────────────────────────────────

def create_embed(title, description="", color=0x1a1a1a, fields=None):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1530142266281164871/1560262829657821236/fc27d6b324431b73e38c600ecb538f09.gif?ex=6ad2137e&is=6ad0c1fe&hm=733aae9ef1edbfa9c8ecf47593040ebbb24eae8d81a866563e85fe1102ba24ed&")
    if fields:
        for field in fields:
            embed.add_field(name=f"▸ {field['name']}", value=field["value"], inline=field.get("inline", False))
    embed.set_footer(
        text=f"{APP_BRAND} | VPS Manager • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        icon_url=""
    )
    return embed

def create_success_embed(title, description=""):
    return create_embed(title, description, color=0x00ff88)

def create_error_embed(title, description=""):
    return create_embed(title, description, color=0xff3366)

def create_info_embed(title, description=""):
    return create_embed(title, description, color=0x00ccff)

def create_warning_embed(title, description=""):
    return create_embed(title, description, color=0xffaa00)

# ─── Docker execution ──────────────────────────────────────────────────────────

async def execute_docker(command, timeout=120):
    """Execute a Docker command with timeout and error handling."""
    try:
        cmd = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            error = stderr.decode().strip() if stderr else "Command failed with no error output"
            raise Exception(error)
        return stdout.decode().strip() if stdout else True
    except asyncio.TimeoutError:
        logger.error(f"Docker command timed out: {command}")
        raise Exception(f"Command timed out after {timeout} seconds")
    except Exception as e:
        logger.error(f"Docker Error: {command} - {str(e)}")
        raise

async def docker_exec(container_name, command, timeout=60):
    """Execute a command inside a running Docker container."""
    cmd = ["docker", "exec", container_name, "bash", "-c", command]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return stdout.decode().strip(), stderr.decode().strip(), proc.returncode

async def create_docker_container(container_name, ram_mb, cpu_count, ssh_port, password, disk_gb=30, image=None):
    """
    Create and configure a Docker container as a VPS.
    `image` can be any supported OS image from OS_OPTIONS.
    """
    image = image or DOCKER_IMAGE

    requested_cpus = float(cpu_count)
    requested_ram_gb = ram_mb / 1024
    try:
        host_cpus = int(await execute_docker("docker info --format '{{.NCPU}}'", timeout=15))
        host_ram_bytes = int(await execute_docker("docker info --format '{{.MemTotal}}'", timeout=15))
    except Exception as e:
        raise RuntimeError(f"Could not read Docker host limits: {e}")
    host_ram_gb = host_ram_bytes / (1024 ** 3)
    cpu_limit = min(host_cpus, MAX_VPS_CPUS) if MAX_VPS_CPUS else host_cpus
    ram_limit_gb = min(host_ram_gb, MAX_VPS_RAM_GB) if MAX_VPS_RAM_GB else host_ram_gb
    if requested_cpus > cpu_limit:
        raise ValueError(f"Requested {requested_cpus:g} CPU cores, but this host allows at most {cpu_limit} per VPS.")
    if requested_ram_gb > ram_limit_gb:
        raise ValueError(f"Requested {requested_ram_gb:g} GB RAM, but this host allows at most {ram_limit_gb:.1f} GB per VPS.")

    reserved_cpus = 0.0
    reserved_ram_gb = 0.0
    for vps_list in vps_data.values():
        for vps in vps_list:
            if vps.get("status") != "running" or vps.get("container_name") == container_name:
                continue
            try:
                reserved_cpus += float(vps.get("cpu", 0))
                reserved_ram_gb += float(str(vps.get("ram", "0")).upper().replace("GB", "").strip())
            except (TypeError, ValueError):
                continue
    total_cpu_limit = min(host_cpus, MAX_TOTAL_VPS_CPUS) if MAX_TOTAL_VPS_CPUS else host_cpus
    total_ram_limit = min(host_ram_gb, MAX_TOTAL_VPS_RAM_GB) if MAX_TOTAL_VPS_RAM_GB else host_ram_gb
    available_cpus = max(0, total_cpu_limit - reserved_cpus)
    available_ram_gb = max(0, total_ram_limit - reserved_ram_gb)
    if requested_cpus > available_cpus:
        raise ValueError(f"Only {available_cpus:g} CPU cores remain within the configured host limit.")
    if requested_ram_gb > available_ram_gb:
        raise ValueError(f"Only {available_ram_gb:.1f} GB RAM remains within the configured host limit.")

    # Pull image if needed (silent)
    try:
        await execute_docker(f"docker pull {image}", timeout=300)
    except Exception:
        pass  # Image might already exist

    # Reuse an existing container with this name instead of failing with
    # "container name is already in use".
    try:
        inspect = await execute_docker(f"docker inspect -f '{{{{.State.Status}}}}' {container_name}", timeout=15)
    except Exception:
        # Missing container is expected during a fresh VPS creation.
        inspect = ""
    if inspect not in (True, "", None):
        state = str(inspect).strip()
        if state != "running":
            await execute_docker(f"docker start {container_name}", timeout=30)
    else:
        run_cmd = (
            f"docker run -d "
            f"--name {container_name} "
            f"--memory={ram_mb}m "
            f"--cpus={cpu_count} "
            f"--privileged "
            f"--restart=unless-stopped "
            f"{image} "
            f"sleep infinity"
        )
        await execute_docker(run_cmd, timeout=60)

    # Ubuntu/Debian use apt; Rocky/Alma/Fedora use dnf.
    if image.startswith(("ubuntu:", "debian:")):
        setup_script = (
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -qq && "
            "apt-get install -y openssh-server tmate tmux curl -qq && "
            "mkdir -p /var/run/sshd /run/sshd && "
            "echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config && "
            "echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config && "
            f"echo 'root:{password}' | chpasswd && "
            "/usr/sbin/sshd"
        )
    else:
        # EPEL is needed for tmate on Rocky/Alma. If it is already present,
        # the command simply continues.
        setup_script = (
            "set -e; "
            "if command -v dnf >/dev/null 2>&1; then "
            "  dnf -y install dnf-plugins-core >/dev/null 2>&1 || true; "
            "  dnf -y install epel-release >/dev/null 2>&1 || true; "
            "  dnf -y install openssh-server tmate tmux curl; "
            "else "
            "  yum -y install openssh-server tmate tmux curl; "
            "fi; "
            "mkdir -p /var/run/sshd /run/sshd; "
            "echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config; "
            "echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config; "
            f"echo 'root:{password}' | chpasswd; "
            "/usr/sbin/sshd"
        )

    stdout, stderr, rc = await docker_exec(container_name, setup_script, timeout=300)
    if rc != 0 and "already" not in stderr.lower():
        raise Exception(f"SSH setup failed: {stderr or stdout}")

    return True


async def ensure_vps_container(vps):
    """Recreate a missing Docker container from the saved VPS record."""
    container_name = vps["container_name"]
    try:
        status = await execute_docker(f"docker inspect -f '{{{{.State.Status}}}}' {container_name}", timeout=15)
    except Exception:
        status = ""
    if status not in (True, "", None) and str(status).strip():
        return
    ram_mb = int(str(vps.get("ram", "1GB")).replace("GB", "")) * 1024
    cpu = int(str(vps.get("cpu", "1")).split()[0])
    disk = int(str(vps.get("storage", "30GB")).replace("GB", ""))
    password = vps.get("ssh_password") or generate_password()
    image = vps.get("os") or DOCKER_IMAGE
    await create_docker_container(
        container_name, ram_mb, cpu, 0, password, disk_gb=disk, image=image
    )
    vps["ssh_password"] = password
    vps["status"] = "running"
    save_data()


async def get_sshx_session(container_name):
    """Start an SSHX web terminal inside the container and return its URL."""
    # Stop an older sshx process so each request creates a fresh temporary link.
    start_script = (
        "pkill -x sshx 2>/dev/null || true; "
        "rm -f /tmp/sshx.log; "
        "nohup sh -c 'curl -sSf https://sshx.io/get | sh -s run' > /tmp/sshx.log 2>&1 </dev/null &"
    )
    stdout, stderr, rc = await docker_exec(container_name, start_script, timeout=15)
    if rc != 0:
        raise Exception(f"SSHX start failed: {stderr or stdout}")

    # sshx prints the public terminal URL after startup. Poll briefly because
    # the installer and tunnel need a few seconds to initialize.
    for _ in range(20):
        await asyncio.sleep(1)
        out, err, _ = await docker_exec(
            container_name,
            "grep -Eo 'https://sshx\\.io/[^[:space:]]+' /tmp/sshx.log 2>/dev/null | head -n 1",
            timeout=10
        )
        match = re.search(r"https://sshx\.io/[^\s]+", out or "")
        if match:
            return re.sub(r'\x1b\[[0-9;]*[A-Za-z]|\[0m', '', match.group(0)).rstrip(').,')

    log_out, log_err, _ = await docker_exec(container_name, "tail -n 40 /tmp/sshx.log 2>/dev/null || true", timeout=10)
    raise Exception(f"SSHX link was not generated. {log_out or log_err}")


async def ensure_tailscaled(container_name):
    """Install/start Tailscale inside a VPS container, waiting for apt locks."""
    install_script = """set -e
if ! command -v tailscale >/dev/null 2>&1; then
    for i in $(seq 1 90); do
        if ! pgrep -x apt-get >/dev/null 2>&1 && ! pgrep -x dpkg >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    export DEBIAN_FRONTEND=noninteractive
    curl -fsSL https://tailscale.com/install.sh | sh
fi
mkdir -p /var/run/tailscale /var/lib/tailscale
if ! pgrep -x tailscaled >/dev/null 2>&1; then
    nohup tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock >/tmp/tailscaled.log 2>&1 </dev/null &
    sleep 2
fi
"""
    stdout, stderr, rc = await docker_exec(container_name, install_script, timeout=240)
    if rc != 0:
        raise Exception(stderr or stdout or "Tailscale installation failed")
    return True


async def start_tailscale_auth(container_name):
    """Start manual Tailscale authorization and wait up to 60 seconds."""

    await ensure_tailscaled(container_name)

    script = (
        "rm -f /tmp/tailscale-up.log; "
        "tailscale logout >/dev/null 2>&1 || true; "
        "tailscale up --accept-dns=false --ssh=false "
        "> /tmp/tailscale-up.log 2>&1 &"
    )

    _, stderr, rc = await docker_exec(
        container_name, script, timeout=15
    )

    if rc != 0:
        raise Exception(
            f"Unable to start Tailscale authorization: {stderr}"
        )

    # Give Tailscale up to 60 seconds to generate the login URL
    for _ in range(60):
        await asyncio.sleep(1)

        out, _, _ = await docker_exec(
            container_name,
            "grep -Eo 'https://login\\.tailscale\\.com/[^[:space:]]+' "
            "/tmp/tailscale-up.log 2>/dev/null | head -n 1",
            timeout=10
        )

        match = re.search(
            r"https://login\.tailscale\.com/[^\s]+",
            out or ""
        )

        if match:
            return match.group(0).rstrip(").,'\"")

        ip_out, _, _ = await docker_exec(
            container_name,
            "tailscale ip -4 2>/dev/null | head -n 1",
            timeout=10
        )

        if re.fullmatch(
            r"100\.\d+\.\d+\.\d+",
            (ip_out or "").strip()
        ):
            return None

    log_out, log_err, _ = await docker_exec(
        container_name,
        "tail -n 50 /tmp/tailscale-up.log 2>/dev/null || true",
        timeout=10
    )

    raise Exception(
        f"Tailscale authorization failed after 60 seconds: "
        f"{log_out or log_err or 'No authorization link generated'}"
    )

async def get_tailscale_ipv4(container_name):
    """Return the VPS's Tailscale IPv4 address, or None if not authorized yet."""
    out, _, rc = await docker_exec(
        container_name,
        "tailscale ip -4 2>/dev/null | head -n 1",
        timeout=10
    )
    ip = (out or "").strip()
    if rc == 0 and re.fullmatch(r"100\.\d+\.\d+\.\d+", ip):
        return ip
    return None


async def wait_and_notify_tailscale(user_id, vps, container_name):
    """Wait for Tailscale authorization and DM the owner once the private IP exists."""
    try:
        for _ in range(120):
            await asyncio.sleep(5)
            try:
                await ensure_vps_container(vps)
                ip = await get_tailscale_ipv4(container_name)
                if ip:
                    vps["tailscale_ip"] = ip
                    vps["tailscale_status"] = "connected"
                    save_data()
                    user = await bot.fetch_user(int(user_id))
                    embed = create_success_embed(
                        "✅ Tailscale Connected!",
                        f"VPS `{container_name}` now has a private IP."
                    )
                    embed.add_field(name="⌯⌲ Private IPv4", value=f"`{ip}`", inline=False)
                    embed.add_field(
                        name="📱 To Connect",
                        value=(
                            "Make sure the Tailscale app is installed on the device you want to connect from, "
                            "logged in with the same account you authorized this VPS with. Then use the IP above "
                            f"like a normal private address (e.g. `ssh root@{ip}`)."
                        ),
                        inline=False
                    )
                    embed.set_footer(text="NovaNode • Cloud Services")
                    await user.send(embed=embed)
                    return
            except Exception as e:
                logger.warning(f"Tailscale wait error for {container_name}: {e}")
    except Exception as e:
        logger.error(f"Tailscale background task failed for {container_name}: {e}")


async def get_tmate_session(container_name):
    """Generate a temporary tmate SSH command, waiting up to 60 seconds."""

    script = (
        "command -v tmate >/dev/null 2>&1 || "
        "(export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update -qq >/dev/null 2>&1 || true; "
        "apt-get install -y tmate >/dev/null 2>&1 || true); "
        "pkill -x tmate 2>/dev/null || true; "
        "rm -f /tmp/tmate.sock /tmp/tmate.log; "
        "tmate -S /tmp/tmate.sock new-session -d "
        ">/tmp/tmate.log 2>&1; "
        "rc=$?; "
        "if [ $rc -ne 0 ]; then "
        "cat /tmp/tmate.log 2>/dev/null || true; exit $rc; "
        "fi; "
        "for i in $(seq 1 60); do "
        "cmd=$(tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}' 2>/dev/null || true); "
        "if printf '%s\\n' \"$cmd\" | grep -q '^ssh '; then "
        "printf '%s\\n' \"$cmd\"; exit 0; "
        "fi; "
        "sleep 1; "
        "done; "
        "echo 'tmate did not generate SSH command within 60 seconds'; "
        "cat /tmp/tmate.log 2>/dev/null || true; "
        "exit 1"
    )

    stdout, stderr, rc = await docker_exec(
        container_name,
        script,
        timeout=70
    )

    if rc != 0 or not stdout.strip():
        raise Exception(
            f"SSH generation failed: {stderr or stdout or 'Unknown tmate error'}"
        )

    return stdout.strip().splitlines()[-1]


# ─── NovaNode AI Request ────────────────────────────────────────────────────

async def NovaNode_ai_answer(question):
    if not OPENROUTER_API_KEY:
        raise RuntimeError('OPENROUTER_API_KEY is not configured.')

    import urllib.request
    import urllib.error

    models = [
        OPENROUTER_MODEL or 'openai/gpt-4o-mini',
        'openrouter/free'
    ]

    last_error = None

    for model in models:
        payload = {
            'model': model,
            'messages': [
                {
                    'role': 'system',
                    'content': Nova_AI_SYSTEM_PROMPT
                },
                {
                    'role': 'user',
                    'content': question
                }
            ],
            'temperature': 0.7,
            'max_completion_tokens': 12000
        }

        request = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'https://NovaNode.host',
                'X-Title': 'NovaNode AI'
            },
            method='POST'
        )

        try:
            loop = asyncio.get_running_loop()

            def send_request():
                try:
                    with urllib.request.urlopen(request, timeout=180) as response:
                        return response.status, response.read().decode('utf-8')
                except urllib.error.HTTPError as e:
                    body = e.read().decode('utf-8', errors='replace')
                    raise RuntimeError(f'HTTP {e.code}: {body[:2000]}')

            status, raw = await loop.run_in_executor(None, send_request)

            data = json.loads(raw)

            if 'error' in data:
                raise RuntimeError(str(data['error']))

            choices = data.get('choices') or []
            if not choices:
                raise RuntimeError(f'No choices returned by {model}: {raw[:1000]}')

            content = choices[0].get('message', {}).get('content')

            if not content:
                raise RuntimeError(f'Empty response from {model}: {raw[:1000]}')

            logger.info(f'NovaNode AI answered using model: {model}')
            return content.strip()

        except Exception as e:
            last_error = e
            logger.warning(f'AI model {model} failed: {e}')
            continue

    raise RuntimeError(f'All AI models failed. Last error: {last_error}')

def NovaNode_ai_direct_answer(question):

    q = question.lower().strip()

    creator_words = (
        "who made you",
        "who created you",
        "who is your creator",
        "who built you",
        "who developed you",
        "kisne banaya",
        "tumhe kisne banaya",
        "tumko kisne banaya",
        "kisne create kiya"
    )

    if any(x in q for x in vps_words):
        return (
            "Sorry, main directly VPS nahi de sakta. 😅\n\n"
            "Aap NovaNode ke **paid plans** ya available "
            "**invite/referral plans** le sakte ho."
        )
    return None


    if not isinstance(loaded, dict):
        logger.warning("vps_data.json has an unknown format, initializing empty data")
        return {}
    normalized = {}
    for uid, value in loaded.items():
        if isinstance(value, dict):
            normalized[uid] = [value] if "container_name" in value else list(value.values())
        elif isinstance(value, list):
            normalized[uid] = value
        else:
            logger.warning(f"Unknown VPS data format for user {uid}, skipping")
    return normalized
# ─── VPS Role helper ───────────────────────────────────────────────────────────

async def get_or_create_vps_role(guild):
    global VPS_USER_ROLE_ID
    if VPS_USER_ROLE_ID:
        role = guild.get_role(VPS_USER_ROLE_ID)
        if role:
            return role
    role = discord.utils.get(guild.roles, name="VPS User")
    if role:
        VPS_USER_ROLE_ID = role.id
        return role
    try:
        role = await guild.create_role(
            name="VPS User",
            color=discord.Color.dark_purple(),
            reason="VPS User role for bot management",
            permissions=discord.Permissions.none()
        )
        VPS_USER_ROLE_ID = role.id
        logger.info(f"Created VPS User role: {role.name} (ID: {role.id})")
        return role
    except Exception as e:
        logger.error(f"Failed to create VPS User role: {e}")
        return None

# ─── CPU Monitor ───────────────────────────────────────────────────────────────

def get_cpu_usage():
    try:
        result = subprocess.run(['top', '-bn1'], capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if '%Cpu(s):' in line:
                for part in line.split(','):
                    if 'id,' in part:
                        idle = float(part.split('%')[0].split()[-1])
                        return 100.0 - idle
        return 0.0
    except Exception as e:
        logger.error(f"Error getting CPU usage: {e}")
        return 0.0

def cpu_monitor():
    global cpu_monitor_active
    while cpu_monitor_active:
        try:
            cpu_usage = get_cpu_usage()
            logger.info(f"Current CPU usage: {cpu_usage}%")
            if cpu_usage > CPU_THRESHOLD:
                logger.warning(f"CPU usage ({cpu_usage}%) exceeded threshold. Stopping all containers.")
                try:
                    subprocess.run(['docker', 'stop', '--time=5'] +
                                   [vps['container_name']
                                    for vps_list in vps_data.values()
                                    for vps in vps_list
                                    if vps.get('status') == 'running'],
                                   check=False)
                    for vps_list in vps_data.values():
                        for vps in vps_list:
                            if vps.get('status') == 'running':
                                vps['status'] = 'stopped'
                    save_data()
                except Exception as e:
                    logger.error(f"Error stopping containers: {e}")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Error in CPU monitor: {e}")
            time.sleep(CHECK_INTERVAL)

cpu_thread = threading.Thread(target=cpu_monitor, daemon=True)
cpu_thread.start()

# ─── Bot events ────────────────────────────────────────────────────────────────

@tasks.loop(minutes=15)
async def help_reminder():
    """Post the literal &help command in the configured server channel every 15 minutes."""
    try:
        channel = bot.get_channel(HELP_REMINDER_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(HELP_REMINDER_CHANNEL_ID)
        if channel:
            await channel.send("&help")
    except Exception as e:
        logger.warning(f"&help reminder failed: {e}")





@bot.tree.command(name="nmsg", description="Send a manual message to a channel")
@app_commands.describe(channel="Channel where the message will be sent", message="Your message")
async def nmsg(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        chunks=[]
        remaining=message
        while len(remaining)>1900:
            cut=remaining.rfind("\n",0,1900)
            if cut<500:
                cut=remaining.jfind(" ",0,1900)
            if cut<1:
                cut=1900
            chunks.append(remaining[:cut])
            remaining=remaining[cut:].lstrip()
        if remaining:
            chunks.append(remaining)
        allowed=discord.AllowedMentions(everyone=True,users=True,roles=True,replied_user=False)
        for chunk in chunks:
            await channel.send(chunk, allowed_mentions=allowed)
        await interaction.followup.send(f"✅ Message sent to {channel.mention} in {len(chunks)} part(s).", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to send messages in that channel or use those emojis.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Discord rejected the message: `{str(e)[:1500]}`", ephemeral=True)
    except Exception as e:
        logger.error(f"/nmsg error: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Failed to send message: `{str(e)[:1500]}`", ephemeral=True)

async def nmsg(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    try:
        await channel.send(message)
        await interaction.response.send_message(f"✅ Message sent to {channel.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I cannot send messages in that channel.", ephemeral=True)
    except Exception as e:
        logger.error(f"nmsg error: {e}")
        await interaction.response.send_message("❌ Failed to send message.", ephemeral=True)

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    try:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        logger.info(f'Slash commands synced to {len(bot.guilds)} guild(s)!')
    except Exception as e:
        logger.error(f'Slash command sync failed: {e}')
    # DND gives the bot a red status indicator while it is online.
    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(type=discord.ActivityType.watching, name=f"{APP_BRAND} | VPS Manager")
    )
    try:
        ensure_all_vps_net()
        await start_web_server()
    except Exception as e:
        logger.error(f"Web terminal failed to start: {e}")
    if not auto_expire_check.is_running():
        auto_expire_check.start()
    logger.info("Bot is ready!")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=create_error_embed("Missing Argument", "Please use `&help` for command usage."))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=create_error_embed("Invalid Argument", "Please check your input and try again."))
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(embed=create_error_embed("System Error", "An error occurred. Please try again."))

def get_os_label(image):
    """Return the friendly OS name for a Docker image."""
    for option in OS_OPTIONS.values():
        if option["image"] == image:
            return option["label"]
    return image or "Unknown"


def get_vps_live_usage(container_name):
    """Read live Docker usage without requiring another bot command."""
    cpu_usage = "0.00"
    memory_usage = "Unknown"
    uptime = "Unknown"

    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", container_name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("|", 1)
            if parts:
                cpu_usage = parts[0].strip().replace("%", "")
            if len(parts) == 2:
                memory_usage = parts[1].strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", container_name],
            capture_output=True, text=True, timeout=10
        )
        started = result.stdout.strip()
        if result.returncode == 0 and started and started != "<no value>":
            started = started.replace("Z", "+00:00")
            started_dt = datetime.fromisoformat(started)
            now = datetime.now(started_dt.tzinfo)
            seconds = max(0, int((now - started_dt).total_seconds()))
            uptime = format_uptime(seconds)
    except Exception:
        pass

    return cpu_usage, memory_usage, uptime


# ─── NovaNode Web Terminal ───────────────────────────────────────────────────
# A small web terminal (Terminus style) that lives inside the bot process.
# People open the site, type the IPv4 / user / password that the bot gave them,
# and get a root shell inside their own VPS container. Nothing else can log in.

import hmac
import pty
import fcntl
import termios
import struct
import signal
from pathlib import Path
from urllib.parse import urlparse
from aiohttp import web, WSMsgType

WEB_HOST = os.getenv("SIFT_WEB_HOST", "0.0.0.0")
# Tries 8082 first and falls back to 8083 if it is already taken.
WEB_PORTS = list(dict.fromkeys([int(os.getenv("PORT") or os.getenv("SIFT_WEB_PORT", "8082")), 8082, 8083]))
# Set this to your public address, e.g. https://terminal.novanode.xyz or http://1.2.3.4:8082
WEB_PUBLIC_URL = os.getenv("SIFT_WEB_URL", "").rstrip("/")
WEB_DIR = Path(__file__).resolve().parent / "web"      # optional: web/index.html overrides the built-in page
WEB_MAX_SESSIONS_PER_VPS = 3
WEB_MAX_FAILS = 6
WEB_FAIL_WINDOW = 5 * 60
WEB_LOCK_SECONDS = 5 * 60

web_active_port = None
_web_runner = None
web_sessions = {}      # container_name -> set of live websockets
_web_fails = {}        # client ip -> [failure timestamps]
_web_locked = {}       # client ip -> unix time when the lock ends


def get_web_url():
    if WEB_PUBLIC_URL:
        return WEB_PUBLIC_URL
    return f"http://YOUR_SERVER_IP:{web_active_port or WEB_PORTS[0]}"


# Fake (but real-looking) IPv4 addresses. They are only a login ID for the site.
_RESERVED_FIRST_OCTETS = {0, 10, 100, 127, 169, 172, 192, 198, 203}

def generate_fake_ipv4():
    used = {v.get("ipv4") for lst in vps_data.values() for v in lst if isinstance(v, dict)}
    while True:
        first = random.choice([n for n in range(11, 224) if n not in _RESERVED_FIRST_OCTETS])
        ip = f"{first}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(2, 254)}"
        if ip not in used:
            return ip

def ensure_vps_net(vps):
    """Give older VPS records an IPv4 the first time it is needed."""
    if not vps.get("ipv4"):
        vps["ipv4"] = generate_fake_ipv4()
        save_data()
    return vps["ipv4"]

def ensure_all_vps_net():
    changed = False
    for lst in vps_data.values():
        for v in lst:
            if isinstance(v, dict) and not v.get("ipv4"):
                v["ipv4"] = generate_fake_ipv4()
                changed = True
    if changed:
        save_data()


_BANNER_GLYPHS = {
        "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
    "I": ["█████", "  █  ", "  █  ", "  █  ", "█████"],
    "F": ["█████", "█    ", "████ ", "█    ", "█    "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "V": ["█   █", "█   █", "█   █", " █ █ ", "  █  "],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
}
_BANNER_COLORS = [(162, 89, 255), (139, 123, 255), (90, 176, 255), (34, 211, 238), (163, 255, 61)]

def build_novanode_banner(cols):
    if cols < 56:
        return "\x1b[1;38;2;184;107;255mN O V A N O D E S\x1b[0m\r\n"
    out = []
    for r in range(5):
        row = " ".join(_BANNER_GLYPHS[c][r] for c in "NOVANODES")
        cr, cg, cb = _BANNER_COLORS[r]
        out.append(f"\x1b[38;2;{cr};{cg};{cb}m{row}\x1b[0m")
    return "\r\n".join(out) + "\r\n"

def build_novanode_banner_markup():
    lines = []
    for row_index, color in enumerate(_BANNER_COLORS):
        row = " ".join(_BANNER_GLYPHS[letter][row_index] for letter in "NOVANODES")
        color_hex = "#" + "".join(f"{channel:02x}" for channel in color)
        lines.append(f'<span style="color:{color_hex}">{row}</span>')
    lines.append('<span class="p">N O V A N O D E S</span>')
    return "\n".join(lines)


def find_vps_by_credentials(ip, user, password):
    """Return (owner_id, vps) when the three values match a VPS, else None."""
    if user != "root":
        return None
    pw = password.encode("utf-8", "ignore")
    match = None
    for owner_id, lst in vps_data.items():
        for v in lst:
            if not isinstance(v, dict):
                continue
            stored = str(v.get("ssh_password", "")).encode("utf-8", "ignore")
            pw_ok = hmac.compare_digest(stored, pw)
            if pw_ok and v.get("ipv4") == ip:
                match = (owner_id, v)
    return match


def _client_ip(request):
    if os.getenv("SIFT_TRUST_PROXY") == "1":
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote or "unknown"

def _lock_remaining(client):
    until = _web_locked.get(client, 0)
    left = int(until - time.time())
    if left <= 0:
        _web_locked.pop(client, None)
        return 0
    return left

def _record_fail(client):
    now = time.time()
    hits = [t for t in _web_fails.get(client, []) if now - t < WEB_FAIL_WINDOW]
    hits.append(now)
    _web_fails[client] = hits
    if len(hits) >= WEB_MAX_FAILS:
        _web_locked[client] = now + WEB_LOCK_SECONDS
        _web_fails.pop(client, None)

def _set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default

async def _pty_write(fd, data):
    view = memoryview(data)
    while len(view):
        try:
            n = os.write(fd, view)
            view = view[n:]
        except BlockingIOError:
            await asyncio.sleep(0.01)


async def kick_web_sessions(container_name, reason="Your login was changed. Please connect again."):
    """Close every open website session of a VPS (used after New IPv4)."""
    for sock in list(web_sessions.get(container_name, [])):
        try:
            if not sock.closed:
                await sock.send_str(json.dumps({"t": "msg", "d": f"\r\n\x1b[1;38;2;255;159;28m{reason}\x1b[0m\r\n"}))
                await sock.close()
        except Exception:
            pass


_RED = "\x1b[1;38;2;255;120;60m"
_VIO = "\x1b[1;38;2;184;107;255m"
_DIM = "\x1b[38;2;138;119;179m"
_RST = "\x1b[0m"


async def web_terminal_ws(request):
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return web.Response(status=403, text="Bad origin")

    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=256 * 1024)
    await ws.prepare(request)
    client = _client_ip(request)

    async def say(text):
        if not ws.closed:
            await ws.send_str(json.dumps({"t": "msg", "d": text}))

    async def fail(text):
        await say(text)
        if not ws.closed:
            await ws.send_str(json.dumps({"t": "fail"}))
            await ws.close()

    # 1) first frame must be the login
    try:
        first = await asyncio.wait_for(ws.receive(), timeout=30)
    except asyncio.TimeoutError:
        await ws.close()
        return ws
    if first.type != WSMsgType.TEXT:
        await ws.close()
        return ws
    try:
        auth = json.loads(first.data)
    except ValueError:
        auth = None
    if not isinstance(auth, dict) or auth.get("t") != "auth":
        await ws.close()
        return ws

    locked = _lock_remaining(client)
    if locked:
        mins = max(1, (locked + 59) // 60)
        await fail(f"{_RED}✖ Too many wrong attempts.{_RST}\r\nTry again in {mins} minute(s).\r\n")
        return ws

    ip = str(auth.get("ip", "")).strip()[:45]
    user = str(auth.get("user", "")).strip()[:32]
    password = str(auth.get("pass", ""))[:128]
    cols = _clamp(auth.get("cols"), 20, 400, 100)
    rows = _clamp(auth.get("rows"), 5, 200, 30)

    # 2) check the credentials against the bot's VPS records
    await asyncio.sleep(0.7)
    found = find_vps_by_credentials(ip, user, password)
    if not found:
        _record_fail(client)
        await asyncio.sleep(0.8)
        await fail(
            f"{_RED}✖ Invalid credentials.{_RST}\r\n"
            "The IPv4, username or password doesn't match any NovaNode VPS.\r\n"
            f"{_DIM}Get a fresh login in Discord: &manage → New IPv4{_RST}\r\n"
        )
        return ws

    owner_id, vps = found
    cname = vps["container_name"]
    _web_fails.pop(client, None)

    if len(web_sessions.get(cname, ())) >= WEB_MAX_SESSIONS_PER_VPS:
        await fail(f"{_RED}✖ Too many open terminals on this VPS.{_RST}\r\nClose one and try again.\r\n")
        return ws

    # 3) valid login → connect
    await say(f"{_VIO}Connecting to {ip}...{_RST}\r\n")
    if vps.get("status") != "running":
        await fail(f"{_RED}✖ This VPS is stopped.{_RST}\r\nStart it from the bot with &manage → Start, then connect again.\r\n")
        return ws
    try:
        await ensure_vps_container(vps)
        state = await execute_docker(f"docker inspect -f '{{{{.State.Running}}}}' {cname}", timeout=15)
        if str(state).strip() != "true":
            await execute_docker(f"docker start {cname}", timeout=60)
    except Exception as e:
        logger.error(f"web terminal: could not prepare {cname}: {e}")
        await fail(f"{_RED}✖ Could not reach the VPS right now.{_RST}\r\nTry again in a moment.\r\n")
        return ws
    tmux_setup = (
        "set -e; "
        "if ! command -v tmux >/dev/null 2>&1; then "
        "  if command -v apt-get >/dev/null 2>&1; then "
        "    export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y tmux -qq; "
        "  elif command -v dnf >/dev/null 2>&1; then dnf -y install tmux; "
        "  elif command -v yum >/dev/null 2>&1; then yum -y install tmux; "
        "  else echo 'No supported package manager found for tmux' >&2; exit 1; fi; "
        "fi; "
        "tmux has-session -t novanode 2>/dev/null || tmux new-session -d -s novanode"
    )
    try:
        _, setup_error, setup_status = await docker_exec(cname, tmux_setup, timeout=240)
        if setup_status != 0:
            raise RuntimeError(setup_error or "tmux setup command failed")
    except Exception as e:
        logger.error(f"web terminal: could not prepare persistent shell on {cname}: {e}")
        await fail(f"{_RED}Could not prepare a persistent terminal session.{_RST}\r\nTry again or contact an administrator.\r\n")
        return ws

    master = slave = None
    proc = None
    loop = asyncio.get_running_loop()
    out_q = asyncio.Queue()
    pump_task = watchdog = None
    last = [time.monotonic()]
    sessions = web_sessions.setdefault(cname, set())
    sessions.add(ws)

    try:
        master, slave = pty.openpty()
        _set_winsize(master, rows, cols)
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-it", "-e", "TERM=xterm-256color", cname,
            "tmux", "attach-session", "-t", "novanode",
            stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
        )
        os.close(slave)
        slave = None
        os.set_blocking(master, False)

        def _on_readable():
            try:
                data = os.read(master, 65536)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if data:
                out_q.put_nowait(data)
            else:
                try:
                    loop.remove_reader(master)
                except Exception:
                    pass
                out_q.put_nowait(None)

        # welcome text first, then the live shell
        await ws.send_str(json.dumps({"t": "ok"}))
        history, _, history_status = await docker_exec(
            cname, "tmux capture-pane -p -S -200 -t novanode", timeout=10
        )
        if history_status == 0 and history:
            await say("\x1b[2J\x1b[H" + history + "\r\n")
        await say(build_novanode_banner(cols))
        await say(f"\r\n{_VIO}Enjoy your time with us!{_RST}\r\n\r\n")
        loop.add_reader(master, _on_readable)

        async def pump():
            while True:
                data = await out_q.get()
                if data is None:
                    break
                if ws.closed:
                    break
                await ws.send_bytes(data)
            await say(f"\r\n{_DIM}[ session ended ]{_RST}\r\n")
            await ws.close()

        async def idle_watch():
            while not ws.closed:
                await asyncio.sleep(30)
                if time.monotonic() - last[0] > WEB_IDLE_SECONDS:
                    idle_minutes = WEB_IDLE_SECONDS // 60
                    await say(f"\r\n{_DIM}[ closed after {idle_minutes} minutes of inactivity; shell remains running ]{_RST}\r\n")
                    await ws.close()
                    return

        pump_task = asyncio.ensure_future(pump())
        watchdog = asyncio.ensure_future(idle_watch())

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type == WSMsgType.ERROR:
                    break
                continue
            last[0] = time.monotonic()
            try:
                m = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            kind = m.get("t")
            if kind == "in":
                d = m.get("d")
                if isinstance(d, str) and len(d) <= 65536:
                    await _pty_write(master, d.encode("utf-8", "ignore"))
            elif kind == "resize":
                c = _clamp(m.get("cols"), 20, 400, cols)
                r = _clamp(m.get("rows"), 5, 200, rows)
                try:
                    _set_winsize(master, r, c)
                    if proc.returncode is None:
                        proc.send_signal(signal.SIGWINCH)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"web terminal error on {cname}: {e}")
    finally:
        sessions.discard(ws)
        if not sessions:
            web_sessions.pop(cname, None)
        for task in (watchdog, pump_task):
            if task:
                task.cancel()
        if master is not None:
            try:
                loop.remove_reader(master)
            except Exception:
                pass
            try:
                os.close(master)
            except OSError:
                pass
        if slave is not None:
            try:
                os.close(slave)
            except OSError:
                pass
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
            except ProcessLookupError:
                pass
        if not ws.closed:
            await ws.close()
    return ws


async def web_index(request):
    html = WEB_INDEX_HTML
    custom = WEB_DIR / "index.html"
    if custom.is_file():
        try:
            html = custom.read_text(encoding="utf-8")
        except OSError:
            pass
    html = html.replace("<!-- NOVANODES_BANNER -->", build_novanode_banner_markup())
    html = html.replace("NovaNode", APP_BRAND).replace("novanode", APP_BRAND.lower())
    return web.Response(text=html, content_type="text/html")

async def web_health(request):
    return web.Response(text="ok")

_WEB_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data:; "
    "connect-src 'self' ws: wss:; frame-ancestors 'none'"
)

@web.middleware
async def _web_security_headers(request, handler):
    resp = await handler(request)
    if not resp.prepared:
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = _WEB_CSP
        resp.headers["Cache-Control"] = "no-store"
    return resp

async def start_web_server():
    global _web_runner, web_active_port
    if _web_runner is not None:
        return
    app = web.Application(middlewares=[_web_security_headers])
    app.router.add_get("/", web_index)
    app.router.add_get("/ws", web_terminal_ws)
    app.router.add_get("/health", web_health)
    runner = web.AppRunner(app)
    await runner.setup()
    for port in WEB_PORTS:
        try:
            await web.TCPSite(runner, WEB_HOST, port).start()
            web_active_port = port
            _web_runner = runner
            logger.info(f"Web terminal is live on port {port}")
            return
        except OSError as e:
            logger.warning(f"Web terminal could not bind port {port}: {e}")
    await runner.cleanup()
    logger.error("Web terminal could not start: ports " + ", ".join(map(str, WEB_PORTS)) + " are all busy.")


WEB_INDEX_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NovaNode Terminal</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%237a2cff'/%3E%3Cpath d='M18 22l14 10-14 10' fill='none' stroke='%23fff' stroke-width='6' stroke-linecap='round' stroke-linejoin='round'/%3E%3Cpath d='M36 44h12' stroke='%23fff' stroke-width='6' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Lexend:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css">
<style>
:root{
  --violet:#a259ff;
  --violet-deep:#6d28d9;
  --violet-soft:#241440;
  --lilac:#c4a1ff;
  --paper:#0b0714;
  --panel:#150d27;
  --ink:#f3ecff;
  --hot:#b86bff;
  --cyan:#22d3ee;
  --lime:#a3ff3d;
  --sun:#ffe14d;
  --orange:#ff9f1c;
  --ui:'Lexend',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --mono:'JetBrains Mono','Cascadia Code',Consolas,Menlo,monospace;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{font-family:var(--ui);color:var(--ink);background:var(--paper);-webkit-font-smoothing:antialiased}
button,input{font:inherit}
:focus-visible{outline:3px solid var(--cyan);outline-offset:3px}

/* ---------- landing ---------- */
#landing{min-height:100%;display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr)}
.left{background:radial-gradient(700px 420px at 15% 0%,rgba(162,89,255,.38),transparent 65%),radial-gradient(500px 360px at 90% 100%,rgba(34,211,238,.16),transparent 65%),#0e0820;color:#fff;display:flex;flex-direction:column;padding:28px clamp(24px,5vw,72px) 40px;border-right:1px solid #2a1850}
.brand{display:flex;align-items:center;gap:12px;font-weight:700;font-size:1.15rem;letter-spacing:.2px}
.brand svg{width:38px;height:38px;flex:none}
.hero{margin:auto 0;max-width:31rem;padding:48px 0}
h1{font-size:clamp(2.4rem,4.6vw,3.9rem);line-height:1.04;margin:0 0 22px;font-weight:700;letter-spacing:-.02em;background:linear-gradient(95deg,#fff 20%,#c4a1ff 60%,#22d3ee);-webkit-background-clip:text;background-clip:text;color:transparent}
.lede{font-size:1.08rem;line-height:1.6;margin:0 0 34px;color:#d9c8ff;max-width:27rem}
.go{display:inline-block;border:0;cursor:pointer;background:var(--violet);color:#fff;font-weight:700;font-size:1.1rem;padding:17px 44px;border-radius:14px;box-shadow:0 6px 0 var(--violet-deep),0 0 34px rgba(162,89,255,.55);transition:transform .12s,box-shadow .12s}
.go:hover{transform:translateY(-2px);box-shadow:0 8px 0 var(--violet-deep),0 0 44px rgba(162,89,255,.75)}
.go:active{transform:translateY(5px);box-shadow:0 1px 0 var(--violet-deep)}
.hint{margin:30px 0 0;font-size:.92rem;line-height:1.6;color:#b9a6e0;max-width:26rem}
.hint code{font-family:var(--mono);background:rgba(34,211,238,.14);padding:2px 7px;border-radius:6px;color:var(--cyan);font-size:.88em}

.right{display:flex;align-items:center;justify-content:center;padding:40px clamp(20px,4vw,64px);background:var(--paper);position:relative;overflow:hidden}
.win{width:100%;max-width:560px;background:var(--panel);border:2px solid var(--violet);border-radius:16px;box-shadow:0 0 0 1px #2a1850,0 18px 70px rgba(162,89,255,.35);overflow:hidden}
.win-bar{display:flex;align-items:center;gap:8px;background:#1d1137;padding:10px 14px;color:#d9c8ff;font-size:.82rem;border-bottom:1px solid #2a1850}
.win-bar i{width:11px;height:11px;border-radius:50%;background:var(--orange)}
.win-bar i:nth-child(2){background:var(--sun)}
.win-bar i:nth-child(3){background:var(--lime)}
.win-bar span{margin-left:8px;font-family:var(--mono)}
.win pre{margin:0;padding:18px 18px 22px;font-family:var(--mono);font-size:clamp(9px,1.05vw,12.5px);line-height:1.5;color:var(--ink);overflow:hidden;white-space:pre}
.win .p{color:var(--cyan);font-weight:700}
.win .ok{color:var(--lime)}
.win .dim{color:#8f7fb8}
.win .cur{display:inline-block;width:.6em;height:1.1em;background:var(--hot);vertical-align:-.2em;animation:blink 1.1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
@media (prefers-reduced-motion:reduce){.win .cur{animation:none}.go{transition:none}}

@media (max-width:820px){
  #landing{grid-template-columns:1fr}
  .right{display:none}
  .left{min-height:100vh;border-right:0}
}

/* ---------- credentials dialog ---------- */
dialog{border:1px solid #3a2270;padding:0;border-radius:20px;width:min(430px,calc(100vw - 28px));background:var(--panel);color:var(--ink);box-shadow:0 0 60px rgba(162,89,255,.4)}
dialog::backdrop{background:rgba(6,3,14,.78);backdrop-filter:blur(4px)}
.dlg-head{background:linear-gradient(120deg,#6d28d9,#a259ff);color:#fff;padding:22px 26px 20px}
.dlg-head h2{margin:0 0 4px;font-size:1.35rem}
.dlg-head p{margin:0;font-size:.9rem;color:#efe4ff}
form{padding:24px 26px 26px;display:grid;gap:16px}
label{display:grid;gap:7px;font-size:.88rem;font-weight:600;color:#d9c8ff}
input{width:100%;padding:13px 14px;border:2px solid #34206a;border-radius:11px;background:var(--paper);color:var(--ink);font-family:var(--mono);font-size:.98rem}
input:focus{outline:none;border-color:var(--cyan);box-shadow:0 0 0 4px rgba(34,211,238,.18)}
.pw{position:relative}
.pw input{padding-right:70px}
.pw button{position:absolute;right:8px;top:50%;transform:translateY(-50%);border:0;background:#2a1850;color:var(--cyan);font-weight:600;font-size:.78rem;padding:7px 11px;border-radius:8px;cursor:pointer}
.row{display:flex;gap:10px;margin-top:4px}
.btn{flex:1;border:0;cursor:pointer;border-radius:12px;padding:14px 18px;font-weight:700;font-size:1rem}
.btn.main{background:var(--violet);color:#fff;box-shadow:0 4px 0 var(--violet-deep),0 0 24px rgba(162,89,255,.4)}
.btn.main:active{transform:translateY(3px);box-shadow:0 1px 0 var(--violet-deep)}
.btn.ghost{background:#2a1850;color:var(--lilac);flex:0 0 auto;padding-inline:22px}
.small{margin:0;font-size:.82rem;line-height:1.5;color:#9a88c4}
.small code{font-family:var(--mono);color:var(--lime);background:rgba(163,255,61,.1);padding:1px 6px;border-radius:5px}

/* ---------- terminal screen ---------- */
#screen{display:none;height:100%;grid-template-rows:auto 1fr;background:var(--paper)}
body.live #landing{display:none}
body.live #screen{display:grid}
.bar{display:flex;align-items:center;gap:14px;background:#150d27;border-bottom:2px solid var(--violet);color:#fff;padding:10px 16px}
.bar .brand{font-size:1rem}
.bar .brand svg{width:28px;height:28px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--sun);flex:none}
.dot.on{background:var(--lime);box-shadow:0 0 10px var(--lime)}
.dot.off{background:var(--orange)}
#who{font-family:var(--mono);font-size:.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;color:var(--cyan)}
.sp{flex:1}
#barbtn{border:0;cursor:pointer;background:var(--violet);color:#fff;font-weight:700;font-size:.88rem;padding:8px 16px;border-radius:10px}
#termwrap{padding:12px 0 8px 14px;min-height:0;background:var(--paper)}
#term{height:100%}
.xterm-viewport{background:transparent!important}
@media (max-width:560px){.bar .brand span{display:none}}
</style>
</head>
<body>

<main id="landing">
  <section class="left">
    <div class="brand">
      <svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="16" fill="#fff"/><path d="M18 22l14 10-14 10" fill="none" stroke="#7a2cff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><path d="M36 44h12" stroke="#7a2cff" stroke-width="6" stroke-linecap="round"/></svg>
      <span>NovaNode</span>
    </div>
    <div class="hero">
      <h1>Your VPS, one tab away.</h1>
      <p class="lede">Type in the IPv4, username and password our bot gave you and you land straight in a root shell. No SSH client, no setup.</p>
      <button class="go" type="button" data-open>Connect</button>
      <p class="hint">Don't have a login yet? In the NovaNode Discord run <code>&amp;manage</code> and press <code>New IPv4</code>.</p>
    </div>
  </section>
  <section class="right" aria-hidden="true">
    <div class="win">
      <div class="win-bar"><i></i><i></i><i></i><span>root@45.132.77.19</span></div>
<pre><span class="dim">Connecting to 45.132.77.19...</span>

<!-- NOVANODES_BANNER -->
<span class="p">Enjoy your time with us!</span>

<span class="p">root@novanode:~#</span> uname -sr
Linux 6.8.0-novanode
<span class="p">root@novanode:~#</span> <span class="cur"></span></pre>
    </div>
  </section>
</main>

<dialog id="dlg" aria-labelledby="dlg-title">
  <div class="dlg-head">
    <h2 id="dlg-title">Connect to your VPS</h2>
    <p>Use the details the NovaNode bot sent you.</p>
  </div>
  <form id="form" autocomplete="off">
    <label>IPv4 address
      <input name="ip" inputmode="decimal" spellcheck="false" placeholder="45.132.77.19" required>
    </label>
    <label>Username
      <input name="user" spellcheck="false" value="root" required>
    </label>
    <label>Password
      <span class="pw">
        <input name="pass" type="password" spellcheck="false" autocomplete="off" required>
        <button type="button" id="eye" aria-label="Show password">Show</button>
      </span>
    </label>
    <div class="row">
      <button class="btn ghost" type="button" id="cancel">Cancel</button>
      <button class="btn main" type="submit">Connect</button>
    </div>
    <p class="small">Lost your login? Run <code>&amp;manage</code> in Discord and press <code>New IPv4</code> to get a fresh one.</p>
  </form>
</dialog>

<div id="screen">
  <header class="bar">
    <div class="brand">
      <svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="16" fill="#fff"/><path d="M18 22l14 10-14 10" fill="none" stroke="#7a2cff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><path d="M36 44h12" stroke="#7a2cff" stroke-width="6" stroke-linecap="round"/></svg>
      <span>NovaNode</span>
    </div>
    <span class="dot" id="dot"></span>
    <span id="who">connecting</span>
    <span class="sp"></span>
    <button id="barbtn" type="button">Disconnect</button>
  </header>
  <div id="termwrap"><div id="term"></div></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
<script>
(function () {
  var $ = function (s) { return document.querySelector(s); };
  var dlg = $('#dlg'), form = $('#form'), dot = $('#dot'), who = $('#who'), barbtn = $('#barbtn');
  var term = null, fit = null, ws = null, live = false, failed = false, label = '';

  var THEME = {
    background: '#0b0714', foreground: '#efe6ff', cursor: '#b86bff', cursorAccent: '#0b0714',
    selectionBackground: 'rgba(184,107,255,.35)',
    black: '#1b1230', red: '#ff5c7a', green: '#a3ff3d', yellow: '#ffe14d',
    blue: '#5a8bff', magenta: '#c77dff', cyan: '#22d3ee', white: '#d9c8ff',
    brightBlack: '#6f5f93', brightRed: '#ff7d95', brightGreen: '#b7ff5e', brightYellow: '#fff07a',
    brightBlue: '#7aa2ff', brightMagenta: '#dba6ff', brightCyan: '#67e8f9', brightWhite: '#ffffff'
  };

  try {
    var saved = JSON.parse(localStorage.getItem('novanode.last') || '{}');
    if (saved.ip) form.ip.value = saved.ip;
    if (saved.user) form.user.value = saved.user;
  } catch (e) {}

  function openDialog() {
    form.pass.value = '';
    form.pass.type = 'password';
    $('#eye').textContent = 'Show';
    if (!dlg.open) dlg.showModal();
    (form.ip.value ? form.pass : form.ip).focus();
  }
  document.querySelectorAll('[data-open]').forEach(function (b) { b.addEventListener('click', openDialog); });
  $('#cancel').addEventListener('click', function () { dlg.close(); });
  dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
  $('#eye').addEventListener('click', function () {
    var show = form.pass.type === 'password';
    form.pass.type = show ? 'text' : 'password';
    this.textContent = show ? 'Hide' : 'Show';
  });

  function setState(kind, text) {
    dot.className = 'dot' + (kind === 'on' ? ' on' : kind === 'off' ? ' off' : '');
    who.textContent = text;
  }

  function ensureTerm() {
    if (term) return;
    term = new Terminal({
      fontFamily: "'JetBrains Mono','Cascadia Code',Consolas,Menlo,monospace",
      fontSize: window.innerWidth < 600 ? 13 : 15,
      lineHeight: 1.15, cursorBlink: true, scrollback: 5000, theme: THEME
    });
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open($('#term'));
    term.onData(function (d) {
      if (live && ws && ws.readyState === 1) ws.send(JSON.stringify({ t: 'in', d: d }));
    });
    var pending = 0;
    new ResizeObserver(function () {
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(function () { fit.fit(); sendSize(); });
    }).observe($('#termwrap'));
  }

  function sendSize() {
    if (live && ws && ws.readyState === 1 && term)
      ws.send(JSON.stringify({ t: 'resize', cols: term.cols, rows: term.rows }));
  }

  function start(ip, user, pass) {
    document.body.classList.add('live');
    ensureTerm();
    term.reset();
    fit.fit();
    live = false; failed = false;
    label = user + '@' + ip;
    setState('', label + ' · authenticating');
    barbtn.textContent = 'Disconnect';

    var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    var sock = new WebSocket(proto + location.host + '/ws');
    sock.binaryType = 'arraybuffer';
    ws = sock;
    var heard = false;

    sock.onopen = function () {
      sock.send(JSON.stringify({ t: 'auth', ip: ip, user: user, pass: pass, cols: term.cols, rows: term.rows }));
    };
    sock.onmessage = function (ev) {
      heard = true;
      if (typeof ev.data !== 'string') { term.write(new Uint8Array(ev.data)); return; }
      var m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.t === 'msg') term.write(m.d);
      else if (m.t === 'ok') { live = true; setState('on', label); fit.fit(); sendSize(); term.focus(); }
      else if (m.t === 'fail') { failed = true; }
    };
    sock.onclose = function () {
      if (sock !== ws) return;
      if (live) term.write('\r\n\x1b[38;2;255;159;28m[ connection closed ]\x1b[0m\r\n');
      else if (!heard) term.write('\x1b[38;2;255;159;28mCould not reach the NovaNode terminal server.\x1b[0m\r\n');
      live = false;
      setState('off', label + ' · offline');
      barbtn.textContent = failed ? 'Try again' : 'Connect again';
    };
    sock.onerror = function () {};
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var ip = form.ip.value.trim(), user = form.user.value.trim() || 'root', pass = form.pass.value;
    try { localStorage.setItem('novanode.last', JSON.stringify({ ip: ip, user: user })); } catch (err) {}
    form.pass.value = '';
    dlg.close();
    start(ip, user, pass);
  });

  barbtn.addEventListener('click', function () {
    if (ws && (ws.readyState === 0 || ws.readyState === 1)) { ws.close(); return; }
    openDialog();
  });

  if (document.fonts && document.fonts.load) {
    document.fonts.load("15px 'JetBrains Mono'");
  }
})();
</script>
</body>
</html>
'''


# ─── ManageView ────────────────────────────────────────────────────────────────

class ManageView(discord.ui.View):
    def __init__(self, user_id, vps_list, is_shared=False, owner_id=None, is_admin=False):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.vps_list = vps_list
        self.selected_index = None
        self.is_shared = is_shared
        self.owner_id = owner_id or user_id
        self.is_admin = is_admin

        if len(vps_list) > 1:
            options = [
                discord.SelectOption(
                    label=f"VPS {i+1} ({v.get('plan', 'Custom')})",
                    description=f"Status: {v.get('status', 'unknown')}",
                    value=str(i)
                ) for i, v in enumerate(vps_list)
            ]
            self.select = discord.ui.Select(placeholder="Select a VPS to manage", options=options)
            self.select.callback = self.select_vps
            self.add_item(self.select)
            self.initial_embed = create_embed("VPS Management", "Select a VPS from the dropdown menu below.", 0x1a1a1a)
            self.initial_embed.add_field(
                name="Available VPS",
                value="\n".join([f"**VPS {i+1}:** `{v['container_name']}` - Status: `{v.get('status','unknown').upper()}`"
                                 for i, v in enumerate(vps_list)]),
                inline=False
            )
        else:
            self.selected_index = 0
            self.initial_embed = self.create_vps_embed(0)
            self.add_action_buttons()

    def create_vps_embed(self, index):
        vps = self.vps_list[index]
        status = vps.get("status", "unknown")
        status_color = 0x00ff88 if status == "running" else 0xff3366

        owner_text = ""
        if self.is_admin and self.owner_id != self.user_id:
            try:
                owner_user = bot.get_user(int(self.owner_id))
                owner_text = f"\n**Owner:** {owner_user.mention}" if owner_user else f"\n**Owner ID:** {self.owner_id}"
            except Exception:
                owner_text = f"\n**Owner ID:** {self.owner_id}"

        cpu_usage, memory_usage, uptime = get_vps_live_usage(vps["container_name"])

        embed = create_embed(
            "🖥️ VPS Management",
            f"Managing VPS `{vps['container_name']}`{owner_text}",
            status_color
        )

        embed.add_field(
            name="⌯⌲ Resources",
            value=(
                f"**Plan:** {vps.get('plan', 'Custom')}\n"
                f"**Status:** `{status.upper()}`\n"
                f"**RAM:** {vps.get('ram', 'Unknown')}\n"
                f"**CPU:** {vps.get('cpu', 'Unknown')} Core(s)\n"
                f"**Storage:** {vps.get('storage', 'Unknown')}\n"
                f"**OS:** {get_os_label(vps.get('os', DOCKER_IMAGE))}\n"
                f"**Uptime:** {uptime}"
            ),
            inline=False
        )

        embed.add_field(
            name="⌯⌲ Live Usage",
            value=(
                f"**CPU Usage:** {cpu_usage}%\n"
                f"**Memory:** {memory_usage}\n"
                f"**Disk:** Unknown"
            ),
            inline=False
        )

        embed.add_field(
            name="⌯⌲ Controls",
            value="Use the buttons below to manage your VPS",
            inline=False
        )
        embed.set_footer(text="NovaNode • Cloud Services")
        return embed

    def add_action_buttons(self):
        if not self.is_shared and not self.is_admin:
            reinstall_button = discord.ui.Button(label="🔄 Reinstall", style=discord.ButtonStyle.danger, row=0)
            reinstall_button.callback = lambda inter: self.action_callback(inter, 'reinstall')
            self.add_item(reinstall_button)

        start_button = discord.ui.Button(label="▶ Start", style=discord.ButtonStyle.success, row=0)
        start_button.callback = lambda inter: self.action_callback(inter, 'start')

        stop_button = discord.ui.Button(label="⏸ Stop", style=discord.ButtonStyle.secondary, row=0)
        stop_button.callback = lambda inter: self.action_callback(inter, 'stop')

        ssh_button = discord.ui.Button(label="🔑 SSH", style=discord.ButtonStyle.primary, row=1)
        ssh_button.callback = lambda inter: self.action_callback(inter, 'ssh')

        sshx_button = discord.ui.Button(label="🔗 SSHX", style=discord.ButtonStyle.success, row=1)
        sshx_button.callback = lambda inter: self.action_callback(inter, 'sshx')

        private_button = discord.ui.Button(label="🌐 Private IP", style=discord.ButtonStyle.primary, row=2)
        private_button.callback = lambda inter: self.action_callback(inter, 'private_ip')

        password_button = discord.ui.Button(label="🔑 Password", style=discord.ButtonStyle.secondary, row=2)
        password_button.callback = lambda inter: self.action_callback(inter, 'password')

        network_button = discord.ui.Button(
            label="🔌 Network & Ports",
            style=discord.ButtonStyle.secondary,
            row=3
        )
        network_button.callback = lambda inter: self.action_callback(inter, 'network_ports')

        ipv4_button = discord.ui.Button(label="🌐 New IPv4", style=discord.ButtonStyle.primary, row=3)
        ipv4_button.callback = lambda inter: self.action_callback(inter, 'new_ipv4')

        self.add_item(start_button)
        self.add_item(stop_button)
        self.add_item(ssh_button)
        self.add_item(sshx_button)
        if self.owner_id == self.user_id:
            self.add_item(private_button)
            self.add_item(password_button)
            self.add_item(network_button)
            self.add_item(ipv4_button)

    async def select_vps(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return
        self.selected_index = int(self.select.values[0])
        new_embed = self.create_vps_embed(self.selected_index)
        self.clear_items()
        self.add_action_buttons()
        await interaction.response.edit_message(embed=new_embed, view=self)

    async def action_callback(self, interaction: discord.Interaction, action: str):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return

        if self.is_shared:
            vps = vps_data[self.owner_id][self.selected_index]
        else:
            vps = self.vps_list[self.selected_index]

        container_name = vps["container_name"]

        if action == 'network_ports':
            await interaction.response.send_message(
                embed=create_info_embed(
                    "🔌 Network & Ports",
                    f"Network configuration for VPS `{container_name}`"
                ),
                ephemeral=True
            )

            embed = create_embed(
                "🔌 NovaNode • Network & Ports",
                f"Connection information for `{container_name}`",
                0x00ccff
            )

            embed.add_field(
                name="🔐 SSH • Default Port",
                value=(
                    "**Port:** `22`\n"
                    "**Protocol:** TCP\n"
                    "**Service:** OpenSSH\n"
                    "Tailscale private IP par SSH ke liye port **22** default hai."
                ),
                inline=False
            )

            tailscale_ip = vps.get("tailscale_ip")

            if tailscale_ip:
                embed.add_field(
                    name="🌐 Tailscale Private Network",
                    value=(
                        f"**Private IP:** `{tailscale_ip}`\n"
                        f"**SSH:** `ssh root@{tailscale_ip} -p 22`"
                    ),
                    inline=False
                )
            else:
                embed.add_field(
                    name="🌐 Tailscale Private Network",
                    value=(
                        "Tailscale abhi connected nahi hai.\n"
                        "Private IP Setup se VPS ko authorize karo."
                    ),
                    inline=False
                )

            embed.add_field(
                name="🔗 Other Ports",
                value=(
                    "Minecraft, web server, API ya kisi aur service ke liye "
                    "jo application port use kare, woh VPS ke andar "
                    "usi port par listen kar sakta hai.\n\n"
                    "Example: `25565`, `8080`, `3000`"
                ),
                inline=False
            )

            embed.add_field(
                name="📱 Termux / PC",
                value=(
                    "Tailscale app me same account se login karke:\n"
                    "```ssh root@100.x.x.x -p 22```"
                ),
                inline=False
            )

            embed.set_footer(text="NovaNode • Cloud Services")

            await interaction.followup.send(
                embed=embed,
                ephemeral=True
            )
            return

        if action == 'reinstall':
            if self.is_shared or self.is_admin:
                await interaction.response.send_message(
                    embed=create_error_embed("Access Denied", "Only the VPS owner can reinstall!"), ephemeral=True)
                return

            parent_view = self

            class ReinstallOSView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=120)
                    self.selected_os = None
                    options = [
                        discord.SelectOption(
                            label=data["label"],
                            value=key,
                            description=data["image"]
                        )
                        for key, data in OS_OPTIONS.items()
                    ]
                    self.os_select = discord.ui.Select(
                        placeholder="Select a new OS...",
                        options=options,
                        min_values=1,
                        max_values=1
                    )
                    self.os_select.callback = self.select_os
                    self.add_item(self.os_select)

                async def select_os(self, inter: discord.Interaction):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message(
                            embed=create_error_embed("Access Denied", "This reinstall menu belongs to another user."),
                            ephemeral=True
                        )
                        return

                    self.selected_os = self.os_select.values[0]
                    os_data = OS_OPTIONS[self.selected_os]
                    self.clear_items()

                    confirm = discord.ui.Button(label="Confirm Reinstall", style=discord.ButtonStyle.danger)
                    cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

                    async def do_confirm(confirm_inter: discord.Interaction):
                        if confirm_inter.user.id != interaction.user.id:
                            await confirm_inter.response.send_message(
                                embed=create_error_embed("Access Denied", "This reinstall menu belongs to another user."),
                                ephemeral=True
                            )
                            return

                        await confirm_inter.response.defer(ephemeral=True)
                        try:
                            await confirm_inter.followup.send(
                                embed=create_info_embed(
                                    "Deleting Container",
                                    f"Removing `{container_name}` before installing **{os_data['label']}**..."
                                ),
                                ephemeral=True
                            )
                            try:
                                await execute_docker(f"docker stop {container_name}")
                            except Exception:
                                pass
                            await execute_docker(f"docker rm -f {container_name}")

                            original_ram = self_vps["ram"]
                            original_cpu = self_vps["cpu"]
                            original_disk = int(self_vps.get("storage", "30GB").replace("GB", ""))
                            ram_mb = int(original_ram.replace("GB", "")) * 1024
                            new_password = generate_password()

                            await confirm_inter.followup.send(
                                embed=create_info_embed(
                                    "Creating New VPS",
                                    f"Installing **{os_data['label']}** on `{container_name}`..."
                                ),
                                ephemeral=True
                            )

                            await create_docker_container(
                                container_name,
                                ram_mb,
                                original_cpu,
                                0,
                                new_password,
                                disk_gb=original_disk,
                                image=os_data["image"]
                            )

                            self_vps["status"] = "running"
                            self_vps["ssh_password"] = new_password
                            self_vps["os"] = os_data["image"]
                            self_vps["created_at"] = datetime.now().isoformat()
                            save_data()

                            await confirm_inter.followup.send(
                                embed=create_success_embed(
                                    "Reinstall Complete",
                                    f"VPS `{container_name}` is now running **{os_data['label']}**."
                                ),
                                ephemeral=True
                            )
                            await interaction.message.edit(
                                embed=parent_view.create_vps_embed(parent_view.selected_index),
                                view=parent_view
                            )
                        except Exception as e:
                            await confirm_inter.followup.send(
                                embed=create_error_embed("Reinstall Failed", f"Error: {str(e)}"),
                                ephemeral=True
                            )

                    async def do_cancel(cancel_inter: discord.Interaction):
                        await cancel_inter.response.edit_message(
                            embed=parent_view.create_vps_embed(parent_view.selected_index),
                            view=parent_view
                        )

                    confirm.callback = do_confirm
                    cancel.callback = do_cancel
                    self.add_item(confirm)
                    self.add_item(cancel)

                    warning = create_warning_embed(
                        "Reinstall Warning",
                        f"⚠️ This will erase all data on `{container_name}` and install **{os_data['label']}**.\n\n"
                        "This action cannot be undone. Continue?"
                    )
                    await inter.response.edit_message(embed=warning, view=self)

            self_vps = vps
            await interaction.response.send_message(
                embed=create_embed(
                    "Select a new OS",
                    "Choose the operating system you want to install on this VPS.",
                    0x1a1a1a
                ),
                view=ReinstallOSView(),
                ephemeral=True
            )

        elif action == 'start':
            await interaction.response.defer(ephemeral=True)
            try:
                await execute_docker(f"docker start {container_name}")
                # Restart SSH inside container
                await docker_exec(container_name, "/usr/sbin/sshd || true", timeout=10)
                vps["status"] = "running"
                save_data()
                await interaction.followup.send(
                    embed=create_success_embed("VPS Started", f"VPS `{container_name}` is now running!"),
                    ephemeral=True)
                await interaction.message.edit(embed=self.create_vps_embed(self.selected_index), view=self)
            except Exception as e:
                await interaction.followup.send(
                    embed=create_error_embed("Start Failed", str(e)), ephemeral=True)

        elif action == 'stop':
            await interaction.response.defer(ephemeral=True)
            try:
                await execute_docker(f"docker stop {container_name}", timeout=120)
                vps["status"] = "stopped"
                save_data()
                await interaction.followup.send(
                    embed=create_success_embed("VPS Stopped", f"VPS `{container_name}` has been stopped!"),
                    ephemeral=True)
                await interaction.message.edit(embed=self.create_vps_embed(self.selected_index), view=self)
            except Exception as e:
                await interaction.followup.send(
                    embed=create_error_embed("Stop Failed", str(e)), ephemeral=True)

        elif action == 'new_ipv4':
            if str(interaction.user.id) != str(self.owner_id):
                await interaction.response.send_message(
                    embed=create_error_embed("Access Denied", "Only the VPS owner can issue a new IPv4."), ephemeral=True)
                return
            if vps.get('status') != 'running':
                await interaction.response.send_message(
                    embed=create_error_embed("New IPv4", "Start the VPS first, then press New IPv4 again."),
                    ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            try:
                await ensure_vps_container(vps)
                new_ip = generate_fake_ipv4()
                new_pass = generate_password()
                out, err, rc = await docker_exec(container_name, f"echo 'root:{new_pass}' | chpasswd", timeout=30)
                if rc != 0:
                    raise Exception(err or out or "could not set the new password")
                vps["ipv4"] = new_ip
                vps["ssh_password"] = new_pass
                save_data()
                await kick_web_sessions(container_name)

                embed = create_success_embed(
                    "🌐 New IPv4 Issued",
                    f"Fresh web terminal login for `{container_name}`. The old IPv4 and password no longer work."
                )
                embed.add_field(name="⌯⌲ Site", value=get_web_url(), inline=False)
                embed.add_field(name="⌯⌲ IPv4", value=f"```{new_ip}```", inline=False)
                embed.add_field(name="⌯⌲ User", value="```root```", inline=False)
                embed.add_field(name="⌯⌲ Password", value=f"```{new_pass}```", inline=False)
                embed.set_footer(text="NovaNode • Cloud Services")
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                await interaction.followup.send(
                    embed=create_error_embed("New IPv4 Failed", str(e)), ephemeral=True)

        elif action == 'password':
            await interaction.response.send_message(
                embed=create_info_embed(
                    "☁️ 🔑 Root Password Set",
                    f"A root password has been generated for `{container_name}`:\n\n"
                    f"⌯⌲ Password\n```{vps.get('ssh_password', 'Password unavailable')}```\n\n"
                    f"⌯⌲ Web Terminal Login\n`root` @ `{ensure_vps_net(vps)}`\n{get_web_url()}\n\n"
                    "NovaNode • Cloud Services"
                ),
                ephemeral=True
            )

        elif action == 'private_ip':
            if vps.get('status') != 'running':
                await interaction.response.send_message(
                    embed=create_error_embed("Private IP Error", "Start the VPS first, then press Private IP again."),
                    ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            try:
                await ensure_vps_container(vps)
                existing_ip = await get_tailscale_ipv4(container_name)
                if existing_ip:
                    vps["tailscale_ip"] = existing_ip
                    vps["tailscale_status"] = "connected"
                    save_data()
                    embed = create_success_embed(
                        "✅ Tailscale Connected!",
                        f"VPS `{container_name}` now has a private IP."
                    )
                    embed.add_field(name="⌯⌲ Private IPv4", value=f"`{existing_ip}`", inline=False)
                    embed.add_field(
                        name="📱 To Connect",
                        value=(
                            "Install Tailscale on your device and log in with the same account used for this VPS. "
                            f"Then connect using `ssh root@{existing_ip}`."
                        ),
                        inline=False
                    )
                    embed.set_footer(text="NovaNode • Cloud Services")
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                await interaction.followup.send(
                    embed=create_info_embed(
                        "🌐 Private IP Setup",
                        f"Setting up your private IP connection for VPS `{container_name}`, this may take a moment..."
                    ),
                    ephemeral=True
                )
                await ensure_tailscaled(container_name)
                await interaction.followup.send(
                    embed=create_success_embed(
                        "✅ Tailscale Installed Successfully",
                        "Tailscale has been installed on your VPS. Continue with the authorization step below."
                    ),
                    ephemeral=True
                )
                auth_url = await start_tailscale_auth(container_name)
                if auth_url:
                    auth_embed = create_info_embed(
                        "🌐 Private IP Setup",
                        f"Click below to authorize VPS `{container_name}` and give it a private IP."
                    )
                    auth_embed.add_field(
                        name="🔗 Step 1: Authorize This VPS",
                        value=f"[Click to Authorize]({auth_url})\nSign in with the Tailscale account you want this VPS linked to.",
                        inline=False
                    )
                    auth_embed.add_field(
                        name="📱 Step 2: Install Tailscale On Your Own Device",
                        value=(
                            "To actually connect to the private IP, download the Tailscale app on your PC/phone and log in with the same account you used above.\n"
                            "https://tailscale.com/download"
                        ),
                        inline=False
                    )
                    auth_embed.add_field(
                        name="⌯⌲ Step 3",
                        value="Once authorized, your VPS's private IP will be sent to you in DM automatically — usually within a minute.",
                        inline=False
                    )
                    auth_embed.set_footer(text="NovaNode • Cloud Services")
                    try:
                        await interaction.user.send(embed=auth_embed)
                        await interaction.followup.send(
                            embed=create_success_embed(
                                "🌐 Private IP Setup",
                                "Authorization link has been sent to your DMs."
                            ),
                            ephemeral=True
                        )
                    except discord.Forbidden:
                        await interaction.followup.send(
                            embed=create_error_embed(
                                "DM Failed",
                                "Please enable DMs so I can send the Tailscale authorization link."
                            ),
                            ephemeral=True
                        )
                else:
                    await interaction.followup.send(
                        embed=create_success_embed(
                            "✅ Tailscale Installed Successfully",
                            "Tailscale is already authorized on this VPS. I am checking for its private IP now and will DM you when it is ready."
                        ),
                        ephemeral=True
                    )

                asyncio.create_task(
                    wait_and_notify_tailscale(str(self.owner_id), vps, container_name)
                )
            except Exception as e:
                await interaction.followup.send(
                    embed=create_error_embed("Tailscale Installation Failed", f"{str(e)}"),
                    ephemeral=True)

        elif action == 'ssh':
            await interaction.response.defer(ephemeral=True)
            try:
                if vps.get('status') != 'running':
                    await interaction.followup.send(
                        embed=create_error_embed("SSH Error", "Start the VPS first, then press SSH again."),
                        ephemeral=True)
                    return

                await interaction.followup.send(
                    embed=create_info_embed("Starting SSH", "Generating a temporary SSH session, please wait..."),
                    ephemeral=True)

                await ensure_vps_container(vps)
                tmate_cmd = await get_tmate_session(container_name)
                now = datetime.now().strftime('%d-%m-%Y %H:%M')
                ssh_embed = create_embed("🔑 NovaNode- SSH Access", f"SSH connection for VPS `{container_name}`:", 0x5865F2)
                ssh_embed.add_field(
                    name="⌯⌲ Command",
                    value=f"```{tmate_cmd}```",
                    inline=False
                )
                ssh_embed.add_field(
                    name="⌯⌲ Security",
                    value="This link is temporary. Do not share it.",
                    inline=False
                )
                ssh_embed.set_footer(text="NovaNode • Cloud Services")

                try:
                    await interaction.user.send(embed=ssh_embed)
                    await interaction.followup.send(
                        embed=create_success_embed("SSH Sent", "Check your DMs for the temporary SSH connection."),
                        ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(
                        embed=create_error_embed("DM Failed", "Enable DMs to receive SSH access."),
                        ephemeral=True)
            except Exception as e:
                await interaction.followup.send(
                    embed=create_error_embed("SSH Error", str(e)), ephemeral=True)

        elif action == 'sshx':
            await interaction.response.defer(ephemeral=True)
            try:
                if vps.get('status') != 'running':
                    await interaction.followup.send(
                        embed=create_error_embed("SSHX Error", "Start the VPS first, then press SSHX again."),
                        ephemeral=True)
                    return

                await interaction.followup.send(
                    embed=create_info_embed("Starting SSHX", "Generating a temporary web terminal, please wait..."),
                    ephemeral=True)

                await ensure_vps_container(vps)
                sshx_url = await get_sshx_session(container_name)
                now = datetime.now().strftime('%d-%m-%Y %H:%M')
                sshx_embed = create_embed("NovaNode • SSHX Access", f"Web SSH connection for VPS `{container_name}`:", 0x00ff88)
                sshx_embed.add_field(
                    name="🔗 Link",
                    value=f"[Click to Open Terminal]({sshx_url})",
                    inline=False
                )
                sshx_embed.add_field(
                    name="⚠️ Security",
                    value="This link grants direct root access. Do not share it.",
                    inline=False
                )
                sshx_embed.set_footer(text=f"Powered by NovaNode • Premium VPS Management • {now}")

                try:
                    await interaction.user.send(embed=sshx_embed)
                    await interaction.followup.send(
                        embed=create_success_embed("SSHX Sent", "Check your DMs for the temporary web terminal."),
                        ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(
                        embed=create_error_embed("DM Failed", "Enable DMs to receive SSHX access."),
                        ephemeral=True)
            except Exception as e:
                await interaction.followup.send(
                    embed=create_error_embed("SSHX Error", str(e)), ephemeral=True)


# ─── Commands ──────────────────────────────────────────────────────────────────

@bot.command(name='ping')
async def ping(ctx):
    """Show the bot's Discord latency with a color based on the ping."""
    ping_ms = round(bot.latency * 1000)

    if ping_ms <= 50:
        color = 0x00FF66
        status = "🟢 Excellent"
    elif ping_ms <= 100:
        color = 0xFFFF00
        status = "🟡 Good"
    elif ping_ms <= 300:
        color = 0xFF69B4
        status = "🩷 High"
    else:
        color = 0xFF0000
        status = "🔴 Very High"

    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"**Ping:** `{ping_ms}ms`\n**Status:** {status}",
        color=color
    )
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1530142266281164871/1560262829657821236/fc27d6b324431b73e38c600ecb538f09.gif?ex=6ad2137e&is=6ad0c1fe&hm=733aae9ef1edbfa9c8ecf47593040ebbb24eae8d81a866563e85fe1102ba24ed&")
    embed.set_footer(text="NovaNode | VPS Manager")
    await ctx.send(embed=embed)


def format_uptime(seconds: int) -> str:
    """Convert seconds into a readable bot runtime string."""
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


@bot.command(name='uptime')
async def uptime(ctx):
    """Show how long the bot has been running."""
    runtime = max(0, int(time.monotonic() - BOT_START_TIME))
    embed = create_info_embed(
        "⏱️ Bot Uptime",
        f"{ctx.author.mention}, the bot has been running for:\n\n"
        f"**`{format_uptime(runtime)}`**"
    )
    embed.add_field(
        name="🟢 Runtime Status",
        value="Bot is online and running.",
        inline=False
    )
    await ctx.send(embed=embed)


def get_localnode_counts():
    """Return active/inactive VPS counts for the Localnode selector."""
    total = 0
    active = 0
    for vps_list in vps_data.values():
        for vps in vps_list:
            total += 1
            if vps.get("status") == "running":
                active += 1
    return active, max(0, total - active)


class CreateVPSView(discord.ui.View):
    """Two-step admin VPS creation: Mode -> OS -> Create."""

    def __init__(self, ctx, user, ram, cpu, disk):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.user = user
        self.ram = ram
        self.cpu = cpu
        self.disk = disk
        self.mode = None
        self.os_key = None

        active, inactive = get_localnode_counts()
        self.mode_select = discord.ui.Select(
            placeholder="Select A Mode To Create Vps",
            options=[
                discord.SelectOption(label=f"Node{i}", value=f"node{i}", description="Standard node • High slot capacity", emoji="🖥️") for i in range(1, 11)
            ] + [
                discord.SelectOption(label=f"Localnode-{i}", value=f"localnode-{i}", description="Local node • High slot capacity", emoji="💻") for i in range(1, 11)
            ],
            min_values=1,
            max_values=1
        )
        self.mode_select.callback = self.select_mode
        self.add_item(self.mode_select)

    async def interaction_allowed(self, interaction):
        return interaction.user.id == self.ctx.author.id

    async def select_mode(self, interaction: discord.Interaction):
        if not await self.interaction_allowed(interaction):
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "This VPS creation menu belongs to another admin."),
                ephemeral=True
            )
            return

        self.mode = self.mode_select.values[0]
        self.clear_items()

        options = [
            discord.SelectOption(
                label=data["label"],
                value=key,
                description=data["image"]
            )
            for key, data in OS_OPTIONS.items()
        ]

        self.os_select = discord.ui.Select(
            placeholder="Select OS To Create",
            options=options,
            min_values=1,
            max_values=1
        )
        self.os_select.callback = self.select_os
        self.add_item(self.os_select)

        active, inactive = get_localnode_counts()
        embed = create_embed(
            "Select OS To Create",
            f"**Node:** `{self.mode}`\n**Active VPS:** `{active}`\n**Inactive VPS:** `{inactive}`\n\nChoose the operating system for `{self.user.display_name}`.",
            0x1a1a1a
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def select_os(self, interaction: discord.Interaction):
        if not await self.interaction_allowed(interaction):
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "This VPS creation menu belongs to another admin."),
                ephemeral=True
            )
            return

        self.os_key = self.os_select.values[0]
        os_data = OS_OPTIONS[self.os_key]

        await interaction.response.defer()
        self.clear_items()

        creating_embed = create_embed(
            "☁️ Creating VPS",
            (
                f"Deploying **{os_data['label']}** for {self.user.mention}\n\n"
                f"**Node:** {self.mode}\n"
                f"**RAM:** {self.ram}GB\n"
                f"**CPU:** {self.cpu} Core(s)\n"
                f"**Storage:** {self.disk}GB\n"
                f"**OS:** {os_data['label']}"
            ),
            0x5865F2
        )
        await interaction.edit_original_response(embed=creating_embed, view=None)

        user_id = str(self.user.id)
        if user_id not in vps_data:
            vps_data[user_id] = []

        vps_count = len(vps_data[user_id]) + 1
        container_name = f"vps-{user_id}-{vps_count}"
        ram_mb = self.ram * 1024
        password = generate_password()
        created_at = datetime.now().isoformat()
        display_time = datetime.now().strftime('%d-%m-%Y %H:%M')

        try:
            await create_docker_container(
                container_name,
                ram_mb,
                self.cpu,
                0,
                password,
                disk_gb=self.disk,
                image=os_data["image"]
            )

            vps_info = {
                "container_name": container_name,
                "ram": f"{self.ram}GB",
                "cpu": str(self.cpu),
                "storage": f"{self.disk}GB",
                "status": "running",
                "os": os_data["image"],
                "created_at": created_at,
                "expires": "Never",
                "ipv4": generate_fake_ipv4(),
                "ssh_password": password,
                "shared_with": [],
                "node": self.mode
            }
            vps_data[user_id].append(vps_info)
            save_data()

            if self.ctx.guild:
                vps_role = await get_or_create_vps_role(self.ctx.guild)
                if vps_role:
                    try:
                        await self.user.add_roles(vps_role, reason="VPS ownership granted")
                    except discord.Forbidden:
                        pass

            server_embed = create_embed("✅ VPS Created Successfully", "", color=0x00ff88)
            server_embed.add_field(name="⌯⌲ Owner", value=self.user.mention, inline=False)
            server_embed.add_field(name="⌯⌲ Container", value=f"`{container_name}`", inline=False)
            server_embed.add_field(
                name="⌯⌲ Resources",
                value=(
                    f"**RAM:** {self.ram} GB\n"
                    f"**CPU:** {self.cpu} Cores\n"
                    f"**Storage:** {self.disk} GB\n"
                    f"**OS:** {os_data['label']}\n"
                    f"**Node:** {self.mode}"
                ),
                inline=False
            )
            server_embed.set_footer(text=f"NovaNode | Op Big Node • {display_time}")
            await self.ctx.send(embed=server_embed)

            dm_embed = create_embed(
                "✅ NovaNode - VPS Created!",
                "Your VPS has been successfully deployed by an admin!",
                color=0x5865F2
            )
            dm_embed.add_field(
                name="⌯⌲ VPS Details",
                value=(
                    f"**VPS ID:** `#{vps_count}`\n"
                    f"**Container Name:** `{container_name}`\n"
                    f"**Configuration:** {self.ram} GB RAM • {self.cpu} CPU Cores • {self.disk} GB Disk\n"
                    f"**Status:** `{vps_info['status']}`\n"
                    f"**OS:** `{os_data['label']}`\n"
                    f"**Node:** `{self.mode}`\n"
                    f"**Created:** {display_time}"
                ),
                inline=False
            )
            dm_embed.add_field(
                name="🌐 Web Terminal Login",
                value=(
                    f"**Site:** {get_web_url()}\n"
                    f"**IPv4:** `{vps_info['ipv4']}`\n"
                    f"**User:** `root`\n"
                    f"**Password:** ||{vps_info['ssh_password']}||"
                ),
                inline=False
            )
            dm_embed.add_field(
                name="⌯⌲ Management",
                value=(
                    "• Use `&manage` to start/stop/reinstall your VPS\n"
                    "• Use `&manage` → SSH/SSHX for terminal access\n"
                    "• Contact admin for upgrades or issues"
                ),
                inline=False
            )
            dm_embed.set_footer(text=f"Powered by NovaNode • Premium VPS Management • {display_time}")
            try:
                await self.user.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        except Exception as e:
            await self.ctx.send(
                embed=create_error_embed("Creation Failed", f"Error: {str(e)}")
            )


@bot.command(name="deploy")
@is_admin()
async def deploy_vps(ctx, user: discord.Member):
    """Admin deployment shortcut using the configured default VPS size."""
    ram = int(ADMIN_DEPLOY_PLAN["ram"])
    cpu = int(ADMIN_DEPLOY_PLAN["cpu"])
    disk = int(ADMIN_DEPLOY_PLAN["disk"])
    active, inactive = get_localnode_counts()
    embed = create_embed(
        "Select Node & OS",
        f"Deploying VPS for {user.mention}.\n\n**RAM:** {ram} GB\n**CPU:** {cpu} Cores\n**Disk:** {disk} GB\n**Available nodes:** Node1–Node10 and Localnode-1–Localnode-10",
        0x71368a,
    )
    await ctx.send(embed=embed, view=CreateVPSView(ctx, user, ram, cpu, disk))


@bot.command(name="create")
@is_admin()
async def create_vps_custom(ctx, user: Optional[discord.Member] = None, ram: Optional[int] = None, cpu: Optional[int] = None, disk: Optional[int] = None):
    """Admin custom VPS: &create @user <ram_GB> <cpu_cores> <storage_GB>"""
    if user is None or ram is None or cpu is None or disk is None:
        await ctx.send(embed=create_error_embed("Usage", "`&create @user <ram_GB> <cpu_cores> <storage_GB>`\nExample: `&create @user 8 4 100`"))
        return
    if ram < 1 or cpu < 1 or disk < 1:
        await ctx.send(embed=create_error_embed("Invalid Values", "RAM, CPU and storage must all be 1 or more."))
        return
    embed = create_embed(
        "Select Node & OS",
        f"Creating a custom VPS for {user.mention}.\n\n**RAM:** {ram} GB\n**CPU:** {cpu} Core(s)\n**Storage:** {disk} GB\n**Available nodes:** Node1\u2013Node10 and Localnode-1\u2013Localnode-10",
        0x71368a,
    )
    await ctx.send(embed=embed, view=CreateVPSView(ctx, user, ram, cpu, disk))


@bot.command(name='manage')
async def manage_vps(ctx, user: discord.Member = None):
    """Manage your VPS or another user's VPS (Admin only)"""
    if user:
        if not (_is_owner(ctx.author.id) or str(ctx.author.id) in admin_data.get("admins", [])):
            await ctx.send(embed=create_error_embed("Access Denied", "Only admins can manage other users' VPS."))
            return
        user_id = str(user.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            await ctx.send(embed=create_error_embed("No VPS Found", f"{user.mention} doesn't have any VPS."))
            return
        view = ManageView(str(ctx.author.id), vps_list, is_admin=True, owner_id=user_id)
        await ctx.send(embed=create_info_embed(f"Managing {user.name}'s VPS", f"Managing VPS for {user.mention}"), view=view)
    else:
        user_id = str(ctx.author.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            embed = create_embed("No VPS Found", "You don't have any VPS. Use `.buywc` to purchase one.", 0xff3366)
            embed.add_field(name="Quick Actions", value="• `&plans` - View plans\n• `&buywc <plan> <processor>` - Purchase VPS", inline=False)
            await ctx.send(embed=embed)
            return
        view = ManageView(user_id, vps_list)
        await ctx.send(embed=view.initial_embed, view=view)


@bot.command(name='delete-vps')
@is_admin()
async def delete_vps(ctx, user: discord.Member, vps_number: int, *, reason: str = "No reason"):
    """Delete a user's VPS (Admin only)"""
    user_id = str(user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or user doesn't have a VPS."))
        return

    vps = vps_data[user_id][vps_number - 1]
    container_name = vps["container_name"]
    await ctx.send(embed=create_info_embed("Deleting VPS", f"Removing VPS #{vps_number}..."))

    try:
        try:
            await execute_docker(f"docker stop {container_name}")
        except:
            pass
        await execute_docker(f"docker rm -f {container_name}")
        del vps_data[user_id][vps_number - 1]
        if not vps_data[user_id]:
            del vps_data[user_id]
            if ctx.guild:
                vps_role = await get_or_create_vps_role(ctx.guild)
                if vps_role and vps_role in user.roles:
                    try:
                        await user.remove_roles(vps_role, reason="No VPS ownership")
                    except discord.Forbidden:
                        pass
        save_data()
        embed = create_success_embed("VPS Deleted Successfully")
        embed.add_field(name="Owner", value=user.mention, inline=True)
        embed.add_field(name="VPS ID", value=f"#{vps_number}", inline=True)
        embed.add_field(name="Container", value=f"`{container_name}`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Deletion Failed", f"Error: {str(e)}"))


@bot.command(name='list-all')
@is_admin()
async def list_all_vps(ctx):
    """List all VPS and user information (Admin only)"""
    embed = create_embed("All VPS Information", "Complete overview of all VPS deployments", 0x1a1a1a)
    total_vps = 0
    running_vps = 0
    stopped_vps = 0
    vps_info = []
    user_summary = []

    for user_id, vps_list in vps_data.items():
        try:
            user = await bot.fetch_user(int(user_id))
            user_vps_count = len(vps_list)
            user_running = sum(1 for vps in vps_list if vps.get('status') == 'running')
            total_vps += user_vps_count
            running_vps += user_running
            stopped_vps += user_vps_count - user_running
            user_summary.append(f"**{user.name}** ({user.mention}) - {user_vps_count} VPS ({user_running} running)")
            for i, vps in enumerate(vps_list):
                status_emoji = "🟢" if vps.get('status') == 'running' else "🔴"
                vps_info.append(
                    f"{status_emoji} **{user.name}** - VPS {i+1}: `{vps['container_name']}` "
                    f"Port:{vps.get('ssh_port','?')} - {vps.get('status','unknown').upper()}"
                )
        except discord.NotFound:
            vps_info.append(f"❓ Unknown User ({user_id}) - {len(vps_list)} VPS")

    embed.add_field(
        name="System Overview",
        value=f"**Total Users:** {len(vps_data)}\n**Total VPS:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {stopped_vps}",
        inline=False
    )
    if user_summary:
        embed.add_field(name="User Summary", value="\n".join(user_summary[:10]), inline=False)
    if vps_info:
        for i in range(0, min(len(vps_info), 30), 15):
            chunk = vps_info[i:i+15]
            embed.add_field(name=f"VPS Deployments ({i+1}-{min(i+15, len(vps_info))})", value="\n".join(chunk), inline=False)
    await ctx.send(embed=embed)


@bot.command(name='manage-shared')
async def manage_shared_vps(ctx, owner: discord.Member, vps_number: int):
    """Manage a shared VPS"""
    owner_id = str(owner.id)
    user_id = str(ctx.author.id)
    if owner_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[owner_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number."))
        return
    vps = vps_data[owner_id][vps_number - 1]
    if user_id not in vps.get("shared_with", []):
        await ctx.send(embed=create_error_embed("Access Denied", "You do not have access to this VPS."))
        return
    view = ManageView(user_id, [vps], is_shared=True, owner_id=owner_id)
    await ctx.send(embed=view.initial_embed, view=view)


@bot.command(name='share-user')
async def share_user(ctx, shared_user: discord.Member, vps_number: int):
    """Share VPS access with another user"""
    user_id = str(ctx.author.id)
    shared_user_id = str(shared_user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number."))
        return
    vps = vps_data[user_id][vps_number - 1]
    if "shared_with" not in vps:
        vps["shared_with"] = []
    if shared_user_id in vps["shared_with"]:
        await ctx.send(embed=create_error_embed("Already Shared", f"{shared_user.mention} already has access!"))
        return
    vps["shared_with"].append(shared_user_id)
    save_data()
    await ctx.send(embed=create_success_embed("VPS Shared", f"VPS #{vps_number} shared with {shared_user.mention}!"))
    try:
        await shared_user.send(embed=create_embed(
            "VPS Access Granted",
            f"You have access to VPS #{vps_number} from {ctx.author.mention}. Use `&manage-shared {ctx.author.mention} {vps_number}`",
            0x00ff88
        ))
    except discord.Forbidden:
        pass


@bot.command(name='share-ruser')
async def revoke_share(ctx, shared_user: discord.Member, vps_number: int):
    """Revoke shared VPS access"""
    user_id = str(ctx.author.id)
    shared_user_id = str(shared_user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number."))
        return
    vps = vps_data[user_id][vps_number - 1]
    if "shared_with" not in vps or shared_user_id not in vps["shared_with"]:
        await ctx.send(embed=create_error_embed("Not Shared", f"{shared_user.mention} doesn't have access!"))
        return
    vps["shared_with"].remove(shared_user_id)
    save_data()
    await ctx.send(embed=create_success_embed("Access Revoked", f"Access to VPS #{vps_number} revoked from {shared_user.mention}!"))


@bot.command(name='buywc')
async def buy_with_credits(ctx, plan: str, processor: str = "Intel"):
    """Buy a VPS with credits"""
    user_id = str(ctx.author.id)
    plan_key = plan.strip().lower()
    plan_data = VPS_PLANS.get(plan_key)
    if not isinstance(plan_data, dict):
        await ctx.send(embed=create_error_embed("Invalid Plan", f"Available: {', '.join(VPS_PLANS)}"))
        return
    processor = processor.title()
    prices = plan_data.get("credits", {})
    if processor not in prices:
        await ctx.send(embed=create_error_embed("Invalid Processor", "Choose: Intel or AMD"))
        return

    cost = int(prices[processor])
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    if user_data[user_id]["credits"] < cost:
        await ctx.send(embed=create_error_embed("Insufficient Credits",
                                                f"You need {cost} credits but have {user_data[user_id]['credits']}"))
        return

    user_data[user_id]["credits"] -= cost
    if user_id not in vps_data:
        vps_data[user_id] = []

    vps_count = len(vps_data[user_id]) + 1
    container_name = f"vps-{user_id}-{vps_count}"
    ram_str = f"{int(plan_data['ram'])}GB"
    cpu_str = str(plan_data["cpu"])
    storage_str = f"{int(plan_data['disk'])}GB"
    ram_mb = int(ram_str.replace("GB", "")) * 1024
    password = generate_password()

    await ctx.send(embed=create_info_embed("Processing Purchase", f"Deploying {plan_key.title()} NovaNode VPS..."))

    try:
        await create_docker_container(container_name, ram_mb, cpu_str, 0, password)

        vps_info = {
            "plan": plan_key.title(),
            "container_name": container_name,
            "ram": ram_str,
            "cpu": cpu_str,
            "storage": storage_str,
            "status": "running",
            "created_at": datetime.now().isoformat(),
            "processor": processor,
            "os": DOCKER_IMAGE,
            "ipv4": generate_fake_ipv4(),
            "ssh_password": password,
            "shared_with": []
        }
        vps_data[user_id].append(vps_info)
        save_data()

        if ctx.guild:
            vps_role = await get_or_create_vps_role(ctx.guild)
            if vps_role:
                try:
                    await ctx.author.add_roles(vps_role, reason="VPS purchase completed")
                except discord.Forbidden:
                    pass

        display_time = datetime.now().strftime('%d-%m-%Y %H:%M')
        embed = create_embed("✅ VPS Created Successfully", "", color=0x00ff88)
        embed.add_field(name="⌯⌲ Owner", value=ctx.author.mention, inline=False)
        embed.add_field(name="⌯⌲ Container", value=f"`{container_name}`", inline=False)
        embed.add_field(
            name="⌯⌲ Resources",
            value=f"**RAM:** {ram_str}\n**CPU:** {cpu_str} Cores\n**Storage:** {storage_str}",
            inline=False
        )
        embed.add_field(name="⌯⌲ OS", value=f"`{DOCKER_IMAGE}`", inline=False)
        embed.set_footer(text=f"NovaNode | Ip Big Node • {display_time}")
        await ctx.send(embed=embed)

        try:
            dm_embed = create_embed(
                "✅ NovaNode - VPS Created!",
                "Your VPS has been successfully deployed by an admin!",
                color=0x5865F2
            )
            dm_embed.add_field(
                name="⌯⌲ VPS Details",
                value=(
                    f"**VPS ID:** `#{vps_count}`\n"
                    f"**Container Name:** `{container_name}`\n"
                    f"**Configuration:** {ram_str} RAM • {cpu_str} CPU Cores • {storage_str} Disk\n"
                    f"**Status:** `{vps_info['status']}`\n"
                    f"**OS:** `{DOCKER_IMAGE}`\n"
                    f"**Created:** {display_time}"
                ),
                inline=False
            )
            dm_embed.add_field(
                name="🌐 Web Terminal Login",
                value=(
                    f"**Site:** {get_web_url()}\n"
                    f"**IPv4:** `{vps_info['ipv4']}`\n"
                    f"**User:** `root`\n"
                    f"**Password:** ||{vps_info['ssh_password']}||"
                ),
                inline=False
            )
            dm_embed.add_field(
                name="⌯⌲ Management",
                value=(
                    "• Use `&manage` to start/stop/reinstall your VPS\n"
                    "• Use `&manage` → SSH for terminal access\n"
                    "• Contact admin for upgrades or issues"
                ),
                inline=False
            )
            dm_embed.add_field(
                name="⌯⌲ Important Notes",
                value=(
                    "• Full root access via SSH\n"
                    "• Docker-ready with nesting and privileged mode\n"
                    "• Back up your data regularly"
                ),
                inline=False
            )
            dm_embed.set_footer(text=f"Powered by NovaNode • Premium VPS Management • {display_time}")
            await ctx.author.send(embed=dm_embed)
        except discord.Forbidden:
            pass

    except Exception as e:
        # Refund credits on failure
        user_data[user_id]["credits"] += cost
        save_data()
        await ctx.send(embed=create_error_embed("Purchase Failed", f"Error: {str(e)}\n\nCredits refunded."))


@bot.command(name='buyc')
async def buy_credits(ctx):
    """Get payment information"""
    embed = create_embed("💳 Purchase Credits", "Choose your payment method below:", 0x1a1a1a)
    embed.add_field(name="🇮🇳 UPI", value="```\n9773230442@fam\n```", inline=False)
    embed.add_field(name="📋 Next Steps", value="1. Pay\n2. Contact admin with transaction ID\n3. Receive credits", inline=False)
    try:
        await ctx.author.send(embed=embed)
        await ctx.send(embed=create_success_embed("Information Sent", "Payment details sent to your DMs!"))
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("DM Failed", "Enable DMs to receive payment info!"))


@bot.command(name="plans")
async def show_plans(ctx):
    embed = create_embed("💎 NovaNode VPS Plans", "Available plans are configured by the server administrator.", 0x71368a)
    for name, plan in VPS_PLANS.items():
        prices = ", ".join(f"{processor}: {price} credits" for processor, price in plan.get("credits", {}).items())
        embed.add_field(
            name=name.title(),
            value=f"**RAM:** {plan['ram']} GB\n**CPU:** {plan['cpu']} core(s)\n**Disk:** {plan['disk']} GB\n**Price:** {prices or 'Configured in settings'}",
            inline=True,
        )
    for name, plan in COIN_PLANS.items():
        embed.add_field(
            name=f"Claim: {name.title()}",
            value=f"**RAM:** {plan['ram']} GB\n**CPU:** {plan.get('cpu', 1)} core(s)\n**Disk:** {plan.get('disk', 10)} GB\n**Price:** {plan['price']} credits",
            inline=True,
        )
    embed.add_field(
        name="Daily reward",
        value=f"`&claim daily` — {_setting_int('daily_claim_credits', 200)} credits every {_setting_int('daily_claim_cooldown_seconds', 86400) // 3600} hours",
        inline=False,
    )
    await ctx.send(embed=embed)


@bot.command(name='bal', aliases=['credits', 'balance'])
async def check_credits(ctx, member: discord.Member = None):
    """Show your balance, or another member's balance with !bal @user."""
    target = member or ctx.author
    user_id = str(target.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
        save_data()
    credits = int(user_data[user_id].get("credits", 0))
    embed = create_embed("💰 Coin Balance", f"**User:** {target.mention}\n**Balance:** `{credits:,}` coins", 0x71368a)
    await ctx.send(embed=embed)


@bot.command(name='give')
@is_admin()
async def give_coins(ctx, member: discord.Member, amount: int):
    """Admin-only command: !give @user 999"""
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Coin amount must be greater than zero."))
        return
    user_id = str(member.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    user_data[user_id]["credits"] = int(user_data[user_id].get("credits", 0)) + amount
    save_data()
    await ctx.send(embed=create_success_embed(
        "🪙 Coins Added",
        f"Added **{amount:,} coins** to {member.mention}.\nNew balance: **{user_data[user_id]['credits']:,} coins**."
    ))


@bot.command(name='adminc')
@is_admin()
async def admin_add_credits(ctx, user: discord.Member, amount: int):
    """Add credits to a user (Admin only)"""
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    user_data[user_id]["credits"] += amount
    save_data()
    await ctx.send(embed=create_success_embed("Credits Added",
                                              f"Added **{amount}** credits to {user.mention}. "
                                              f"New balance: **{user_data[user_id]['credits']}**"))


@bot.command(name='adminrc')
@is_admin()
async def admin_remove_credits(ctx, user: discord.Member, amount: str):
    """Remove credits from a user (Admin only). Use 'all' to remove all."""
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    if amount.lower() == "all":
        removed = user_data[user_id]["credits"]
        user_data[user_id]["credits"] = 0
    else:
        removed = int(amount)
        user_data[user_id]["credits"] = max(0, user_data[user_id]["credits"] - removed)
    save_data()
    await ctx.send(embed=create_success_embed("Credits Removed",
                                              f"Removed **{removed}** credits from {user.mention}. "
                                              f"New balance: **{user_data[user_id]['credits']}**"))


@bot.command(name='userinfo')
@is_admin()
async def user_info(ctx, user: discord.Member):
    """Get detailed user information (Admin only)"""
    user_id = str(user.id)
    credits = user_data.get(user_id, {}).get("credits", 0)
    embed = create_embed(f"👤 User Info - {user.name}", "", 0x1a1a1a)
    embed.add_field(name="User", value=f"{user.mention}\n**ID:** {user.id}", inline=False)
    embed.add_field(name="💰 Credits", value=f"**{credits}**", inline=True)
    vps_list = vps_data.get(user_id, [])
    if vps_list:
        vps_text = "\n".join([
            f"VPS {i+1}: `{v['container_name']}` | {v.get('status','?').upper()}"
            for i, v in enumerate(vps_list)
        ])
        embed.add_field(name="🖥️ VPS", value=vps_text, inline=False)
    else:
        embed.add_field(name="🖥️ VPS", value="No VPS owned", inline=False)
    is_admin_user = _is_owner(user_id) or user_id in admin_data.get("admins", [])
    embed.add_field(name="🛡️ Admin", value="Yes" if is_admin_user else "No", inline=True)
    await ctx.send(embed=embed)


@bot.command(name='serverstats')
@is_admin()
async def server_stats(ctx):
    """Show server statistics (Admin only)"""
    total_vps = sum(len(v) for v in vps_data.values())
    running_vps = sum(1 for vl in vps_data.values() for v in vl if v.get('status') == 'running')
    total_credits = sum(u.get('credits', 0) for u in user_data.values())
    total_ram = sum(int(v['ram'].replace('GB', '')) for vl in vps_data.values() for v in vl)
    total_cpu = sum(int(v['cpu']) for vl in vps_data.values() for v in vl)

    embed = create_embed("📊 Server Statistics", "Current server overview", 0x1a1a1a)
    embed.add_field(name="👥 Users", value=f"**Total:** {len(user_data)}\n**Admins:** {len(admin_data.get('admins', [])) + 1}", inline=False)
    embed.add_field(name="🖥️ VPS", value=f"**Total:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {total_vps - running_vps}", inline=False)
    embed.add_field(name="💰 Economy", value=f"**Total Credits:** {total_credits}", inline=False)
    embed.add_field(name="📈 Resources", value=f"**Total RAM:** {total_ram}GB\n**Total CPU:** {total_cpu} cores", inline=False)
    await ctx.send(embed=embed)


@bot.command(name='vpsinfo')
@is_admin()
async def vps_info(ctx, container_name: str = None):
    """Get VPS information (Admin only)"""
    if not container_name:
        all_vps = []
        for uid, vl in vps_data.items():
            try:
                u = await bot.fetch_user(int(uid))
                for i, v in enumerate(vl):
                    all_vps.append(f"**{u.name}** - VPS {i+1}: `{v['container_name']}` - {v.get('status','?').upper()}")
            except:
                pass
        embed = create_embed("🖥️ All VPS", f"Total: {len(all_vps)}", 0x1a1a1a)
        for i in range(0, len(all_vps), 20):
            embed.add_field(name=f"VPS List ({i+1}-{i+20})", value="\n".join(all_vps[i:i+20]), inline=False)
        await ctx.send(embed=embed)
    else:
        found_vps = None
        found_user = None
        for uid, vl in vps_data.items():
            for v in vl:
                if v['container_name'] == container_name:
                    found_vps = v
                    found_user = await bot.fetch_user(int(uid))
                    break
            if found_vps:
                break
        if not found_vps:
            await ctx.send(embed=create_error_embed("Not Found", f"No VPS with name `{container_name}`"))
            return
        embed = create_embed(f"🖥️ VPS - {container_name}", f"Owned by {found_user.mention}", 0x1a1a1a)
        embed.add_field(name="Specs", value=f"**RAM:** {found_vps['ram']}\n**CPU:** {found_vps['cpu']} Cores", inline=True)
        embed.add_field(name="Status", value=f"**{found_vps.get('status','?').upper()}**", inline=True)
        embed.add_field(name="Created", value=found_vps.get('created_at', 'Unknown'), inline=False)
        await ctx.send(embed=embed)


@bot.command(name='restart-vps')
@is_admin()
async def restart_vps(ctx, container_name: str):
    """Restart a VPS (Admin only)"""
    await ctx.send(embed=create_info_embed("Restarting VPS", f"Restarting `{container_name}`..."))
    try:
        await execute_docker(f"docker restart {container_name}")
        # Restart SSH
        await asyncio.sleep(3)
        await docker_exec(container_name, "/usr/sbin/sshd || true", timeout=10)
        for vl in vps_data.values():
            for v in vl:
                if v['container_name'] == container_name:
                    v['status'] = 'running'
                    save_data()
                    break
        await ctx.send(embed=create_success_embed("VPS Restarted", f"`{container_name}` restarted successfully!"))
    except Exception as e:
        await ctx.send(embed=create_error_embed("Restart Failed", str(e)))


@bot.command(name='backup-vps')
@is_admin()
async def backup_vps(ctx, container_name: str):
    """Create a Docker image snapshot of a VPS (Admin only)"""
    snapshot_name = f"{container_name}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    await ctx.send(embed=create_info_embed("Creating Backup", f"Committing snapshot of `{container_name}`..."))
    try:
        await execute_docker(f"docker commit {container_name} {snapshot_name}")
        await ctx.send(embed=create_success_embed("Backup Created", f"Image `{snapshot_name}` created!"))
    except Exception as e:
        await ctx.send(embed=create_error_embed("Backup Failed", str(e)))


@bot.command(name='restore-vps')
@is_admin()
async def restore_vps(ctx, container_name: str, snapshot_name: str):
    """Restore a VPS from a Docker snapshot image (Admin only)"""
    await ctx.send(embed=create_info_embed("Restoring VPS", f"Restoring `{container_name}` from `{snapshot_name}`..."))
    try:
        # Find VPS info for port/password
        found_vps = None
        for vl in vps_data.values():
            for v in vl:
                if v['container_name'] == container_name:
                    found_vps = v
                    break
        if not found_vps:
            await ctx.send(embed=create_error_embed("Not Found", f"No VPS data for `{container_name}`"))
            return

        # Stop and remove current container
        try:
            await execute_docker(f"docker stop {container_name}")
        except:
            pass
        await execute_docker(f"docker rm -f {container_name}")

        # Recreate from snapshot image
        ssh_port = found_vps.get("ssh_port", get_next_ssh_port())
        run_cmd = (
            f"docker run -d "
            f"--name {container_name} "
            f"-p {ssh_port}:22 "
            f"--restart=unless-stopped "
            f"{snapshot_name} "
            f"sleep infinity"
        )
        await execute_docker(run_cmd)
        await asyncio.sleep(2)
        await docker_exec(container_name, "/usr/sbin/sshd || true", timeout=10)
        found_vps["status"] = "running"
        save_data()
        await ctx.send(embed=create_success_embed("VPS Restored", f"`{container_name}` restored from `{snapshot_name}`1"))
    except Exception as e:
        await ctx.send(embed=create_error_embed("Restore Failed", str(e)))


@bot.command(name='list-snapshots')
@is_admin()
async def list_snapshots(ctx, container_name: str):
    """List Docker image snapshots for a VPS (Admin only)"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "images", "--format", "{{.Repository}}:{{.Tag}} ({{.Size}})",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        all_images = stdout.decode().strip().split('\n')
        snapshots = [img for img in all_images if img.startswith(container_name + "-backup-")]
        if snapshots:
            embed = create_embed(f"📸 Snapshots for {container_name}", f"Found {len(snapshots)} snapshots", 0x1a1a1a)
            embed.add_field(name="Snapshots", value="\n".join([f"• `{s}`" for s in snapshots]), inline=False)
        else:
            embed = create_info_embed("No Snapshots", f"No snapshots found for `{container_name}`")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Error", str(e)))


@bot.command(name='exec')
@is_admin()
async def execute_command(ctx, container_name: str, *, command: str):
    """Execute a command inside a VPS container (Admin only)"""
    await ctx.send(embed=create_info_embed("Executing Command", f"Running in `{container_name}`..."))
    try:
        stdout, stderr, rc = await docker_exec(container_name, command, timeout=30)
        embed = create_embed(f"Command Output - {container_name}", f"Command: `{command}`", 0x1a1a1a)
        if stdout:
            out = stdout[:1000] + "\n...(truncated)" if len(stdout) > 1000 else stdout
            embed.add_field(name="📤 Output", value=f"```\n{out}\n```", inline=False)
        if stderr:
            err = stderr[:1000] + "\n...(truncated)" if len(stderr) > 1000 else stderr
            embed.add_field(name="⚠️ Stderr", value=f"```\n{err}\n```", inline=False)
        embed.add_field(name="🔄 Exit Code", value=f"**{rc}**", inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Execution Failed", str(e)))


@bot.command(name='stop-vps-all')
@is_admin()
async def stop_all_vps(ctx):
    """Stop all VPS containers (Admin only)"""
    await ctx.send(embed=create_warning_embed("Stopping All VPS",
                                              "⚠️ This will stop ALL running VPS. Continue?"))

    class ConfirmView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="Stop All VPS", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, item: discord.ui.Button):
            await interaction.response.defer()
            stopped_count = 0
            errors = []
            for vl in vps_data.values():
                for v in vl:
                    if v.get('status') == 'running':
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "docker", "stop", v['container_name'],
                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                            )
                            await proc.communicate()
                            v['status'] = 'stopped'
                            stopped_count += 1
                        except Exception as e:
                            errors.append(str(e))
            save_data()
            embed = create_success_embed("All VPS Stopped", f"Stopped **{stopped_count}** containers.")
            if errors:
                embed.add_field(name="Errors", value="\n".join(errors[:5]), inline=False)
            await interaction.followup.send(embed=embed)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
            await interaction.response.edit_message(embed=create_info_embed("Cancelled", "Operation cancelled."))

    await ctx.send(view=ConfirmView())


@bot.command(name='cpu-monitor')
@is_admin()
async def cpu_monitor_control(ctx, action: str = "status"):
    """Control CPU monitoring (Admin only)"""
    global cpu_monitor_active
    if action.lower() == "status":
        status = "Active" if cpu_monitor_active else "Inactive"
        embed = create_embed("CPU Monitor Status", f"Status: **{status}**",
                             0x00ccff if cpu_monitor_active else 0xffaa00)
        embed.add_field(name="Threshold", value=f"{CPU_THRESHOLD}%", inline=True)
        embed.add_field(name="Check Interval", value=f"{CHECK_INTERVAL}s", inline=True)
        await ctx.send(embed=embed)
    elif action.lower() == "enable":
        cpu_monitor_active = True
        await ctx.send(embed=create_success_embed("CPU Monitor Enabled"))
    elif action.lower() == "disable":
        cpu_monitor_active = False
        await ctx.send(embed=create_warning_embed("CPU Monitor Disabled"))
    else:
        await ctx.send(embed=create_error_embed("Invalid Action", "Use: `&cpu-monitor <status|enable|disable>`"))


@bot.command(name='admin-add')
@is_main_admin()
async def admin_add(ctx, user: discord.Member):
    user_id = str(user.id)
    if user_id not in admin_data["admins"]:
        admin_data["admins"].append(user_id)
        save_data()
    await ctx.send(embed=create_success_embed("Admin Added", f"{user.mention} is now an admin."))


@bot.command(name="owner-add")
@is_main_admin()
async def owner_add(ctx, user: discord.Member):
    owners = admin_data.setdefault("owners", [str(MAIN_ADMIN_ID)])
    if str(user.id) not in owners:
        owners.append(str(user.id))
    if str(user.id) not in admin_data.setdefault("admins", []):
        admin_data["admins"].append(str(user.id))
    save_data()
    await ctx.send(embed=create_success_embed("Main Admin Added", f"{user.mention} has been added as a main admin/owner."))


@bot.command(name='admin-remove')
@is_main_admin()
async def admin_remove(ctx, user: discord.Member):
    user_id = str(user.id)
    if user_id in admin_data["admins"]:
        admin_data["admins"].remove(user_id)
        save_data()
    await ctx.send(embed=create_success_embed("Admin Removed", f"{user.mention} is no longer an admin."))


@bot.command(name='admin-list')
@is_main_admin()
async def admin_list(ctx):
    admins = []
    for aid in admin_data.get("admins", []):
        try:
            u = await bot.fetch_user(int(aid))
            admins.append(f"• {u.mention} ({u.name})")
        except:
            admins.append(f"• Unknown ({aid})")
    embed = create_info_embed("Admin List", "\n".join(admins) if admins else "No admins")
    await ctx.send(embed=embed)




# ─── Coins, coupons and self-service VPS claims ────────────────────────────────
COUPON_FILE = os.path.join(APP_DIR, "coupons.json")
def load_coupons():
    try:
        with open(COUPON_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except (OSError, json.JSONDecodeError): return {}
coupons = load_coupons()
COIN_PLANS = SETTINGS.get("coin_plans", {
    "starter": {"ram": 1, "cpu": 1, "disk": 10, "price": 1000},
    "standard": {"ram": 2, "cpu": 1, "disk": 20, "price": 2000},
})

@bot.command(name="coupon")
@is_admin()
async def coupon_command(ctx, action: str, code: str, total_slot: int = 1, money: int = 0):
    if action.lower() != "create":
        await ctx.send("Usage: `&coupon create <code> <total_slot> <money>`"); return
    if total_slot < 1 or money < 0:
        await ctx.send("Slots must be at least 1 and money cannot be negative."); return
    code = code.upper()
    coupons[code] = {"slots": total_slot, "money": money, "redeemed": []}
    with open(COUPON_FILE, "w", encoding="utf-8") as f: json.dump(coupons, f, indent=2)
    await ctx.send(embed=create_success_embed("Coupon Created", f"Code: `{code}`\nSlots: `{total_slot}`\nCoins per redemption: `{money}`"))

@bot.command(name="redeem")
async def redeem_coupon(ctx, code: str):
    code = code.upper(); entry = coupons.get(code)
    if not entry or len(entry.get("redeemed", [])) >= int(entry.get("slots", 0)):
        await ctx.send(embed=create_error_embed("Invalid Coupon", "This coupon is invalid or has no slots left.")); return
    uid = str(ctx.author.id)
    if uid in entry.setdefault("redeemed", []):
        await ctx.send(embed=create_error_embed("Already Redeemed", "You have already used this coupon.")); return
    ud = user_data.setdefault(uid, {"credits": 0})
    ud["credits"] = int(ud.get("credits", 0)) + int(entry.get("money", 0))
    entry["redeemed"].append(uid); save_data()
    with open(COUPON_FILE, "w", encoding="utf-8") as f: json.dump(coupons, f, indent=2)
    await ctx.send(embed=create_success_embed("Coupon Redeemed", f"Added **{entry['money']} coins**. Balance: **{ud['credits']} coins**."))

@bot.command(name="claim")
async def claim_command(ctx, action: str = "menu"):
    uid = str(ctx.author.id); ud = user_data.setdefault(uid, {"credits": 0})
    if action.lower() == "daily":
        now = time.time(); last = float(ud.get("daily_claim_at", 0))
        daily_cooldown = _setting_int("daily_claim_cooldown_seconds", 86400)
        daily_reward = _setting_int("daily_claim_credits", 200)
        remaining = daily_cooldown - (now - last)
        if remaining > 0:
            hours, rem = divmod(int(remaining), 3600); minutes = rem // 60
            await ctx.send(embed=create_info_embed("Daily Reward Cooldown", f"You can claim 200 coins in **{hours}h {minutes}m**.")); return
        ud["credits"] = int(ud.get("credits", 0)) + daily_reward; ud["daily_claim_at"] = now; save_data()
        await ctx.send(embed=create_success_embed("Daily Reward Claimed", f"+{daily_reward} coins! Balance: **{ud['credits']} coins**.")); return
    if action.lower() not in ("menu", "now"):
        await ctx.send("Use `&claim`, `&claim now`, or `&claim daily`."); return
    class ClaimView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=120)
        @discord.ui.button(label="Claim Now", style=discord.ButtonStyle.success)
        async def claim_now(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("This claim menu belongs to another user.", ephemeral=True); return
            # Default self-service plan: the largest plan the balance can afford.
            current = user_data.setdefault(str(interaction.user.id), {"credits": 0})
            balance = int(current.get("credits", 0))
            chosen = next((key for key, plan in sorted(COIN_PLANS.items(), key=lambda item: int(item[1]["ram"]), reverse=True) if balance >= int(plan["price"])), None)
            if not chosen:
                await interaction.response.send_message(f"Not enough coins. Balance: {balance}. Use `&plans`.", ephemeral=True); return
            await interaction.response.defer(ephemeral=True, thinking=True)
            spec = COIN_PLANS[chosen]; ram = int(spec["ram"]); cpu = int(spec.get("cpu", 1)); disk = int(spec.get("disk", 10))
            container_name = f"vps-{interaction.user.id}-{len(vps_data.get(str(interaction.user.id), []))+1}"
            password = generate_password(); ssh_port = get_next_ssh_port()
            node = f"node{(len(vps_data.get(str(interaction.user.id), [])) % 10)+1}"
            try:
                await create_docker_container(container_name, ram * 1024, cpu, ssh_port, password, disk_gb=disk, image=DOCKER_IMAGE)
                current["credits"] = balance - spec["price"]
                vps_data.setdefault(str(interaction.user.id), []).append({"container_name":container_name,"ram":f"{ram}GB","cpu":str(cpu),"storage":f"{disk}GB","status":"running","os":DOCKER_IMAGE,"created_at":datetime.now().isoformat(),"expires":"Never","ipv4":generate_fake_ipv4(),"ssh_password":password,"ssh_port":ssh_port,"shared_with":[],"node":node})
                save_data()
                await interaction.followup.send(f"✅ VPS deployed! **{ram}GB RAM • {cpu} CPU • {disk}GB disk** on `{node}`. Web terminal login: use `&manage` → **🌐 New IPv4**. Remaining balance: **{current['credits']} coins**. Container: `{container_name}`", ephemeral=True)
            except Exception as exc:
                await interaction.followup.send(f"❌ VPS deployment failed: `{exc}`. Your coins were not deducted.", ephemeral=True)
    embed = create_embed("VPS Claim", f"**Your balance:** {int(ud.get('credits', 0))} coins\nPress **Claim Now** to automatically choose the largest affordable plan. Press Cancel to close.", 0x71368a)
    class CancelView(ClaimView):
        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("This menu belongs to another user.", ephemeral=True); return
            await interaction.response.edit_message(content="Claim cancelled.", embed=None, view=None)
    await ctx.send(embed=embed, view=CancelView())

# ─── Port Forwarding ──────────────────────────────────────────────────────────

@bot.command(name="pcreate")
async def port_create(ctx, vps_id: str, port: Optional[int] = None):
    """Create a port forward for a VPS."""

    user_id = str(ctx.author.id)
    user_vps = vps_data.get(user_id, [])

    target = None

    for index, vps in enumerate(user_vps):
        if (
            str(vps.get("id", "")) == str(vps_id)
            or str(vps.get("vps_id", "")) == str(vps_id)
            or str(index + 1) == str(vps_id)
        ):
            target = vps
            break

    if not target:
        await ctx.send(
            embed=create_error_embed(
                "VPS Not Found",
                f"No VPS found with ID `{vps_id}`."
            )
        )
        return

    # Default random 2-digit port
    if port is None:
        used = set()

        for values in port_forwards.values():
            for item in values:
                try:
                    used.add(int(item["port"]))
                except:
                    pass

        available = [
            x for x in range(10, 100)
            if x not in used and x != 22
        ]

        if not available:
            await ctx.send(
                embed=create_error_embed(
                    "No Port Available",
                    "No free 2-digit port is available."
                )
            )
            return

        port = random.choice(available)

    if port < 10 or port > 65535:
        await ctx.send(
            embed=create_error_embed(
                "Invalid Port",
                "Port must be between `10` and `65535`."
            )
        )
        return

    # Port 22 is reserved for SSH
    if port == 22:
        await ctx.send(
            embed=create_error_embed(
                "Port 22 Reserved",
                "Port `22` is reserved for SSH."
            )
        )
        return

    key = str(ctx.author.id)

    if key not in port_forwards:
        port_forwards[key] = []

    # Prevent duplicate port for same user
    if any(int(x["port"]) == port for x in port_forwards[key]):
        await ctx.send(
            embed=create_error_embed(
                "Port Already Exists",
                f"Port `{port}` is already allocated."
            )
        )
        return

    forward_id = random.randint(100000, 999999)

    record = {
        "id": forward_id,
        "vps_id": str(vps_id),
        "container_name": target.get("container_name", ""),
        "port": port,
        "target_port": port,
        "protocol": "tcp",
        "created_by": key,
        "created_at": datetime.now().isoformat()
    }

    port_forwards[key].append(record)

    # Save inside user VPS data too
    target.setdefault("port_forwards", [])
    target["port_forwards"].append(record)

    save_data()

    await ctx.send(
        embed=create_success_embed(
            "🔌 Port Forwarded",
            f"Port `{port}` has been allocated to VPS `{vps_id}`.\n\n"
            f"**Forward:** `HOST:{port} → VPS:{port}`\n"
            f"**Protocol:** `TCP`\n"
            f"**Port ID:** `{forward_id}`\n\n"
            "🔐 SSH port `22` remains reserved for SSH/Tailscale."
        )
    )


@bot.command(name="ports")
async def ports_help(ctx):
    """Show port forwarding information."""

    embed = create_embed(
        "📚 Nova Help - 🔌 Port Forwarding",
        "Manage port forwarding for your VPS",
        0x00ccff
    )

    embed.add_field(
        name="⌯⌲ Commands",
        value=(
            "**`&ports`** - Port forwarding help\n"
            "**`&pcreate <vps_id> <port>`** - Add port forward\n"
            "**`&pcreate <vps_id>`** - Add random 2-digit port\n"
            "**`&ports list`** - List your port forwards\n"
            "**`&ports remove <id>`** - Remove port forward"
        ),
        inline=False
    )

    embed.add_field(
        name="⌯⌲ SSH",
        value=(
            "**Port `22`** is reserved for SSH.\n"
            "Tailscale private IP uses SSH on port `22`."
        ),
        inline=False
    )

    embed.add_field(
        name="⌯⌲ Navigation",
        value=(
            "• Use `&pcreate <vps_id>` for a random 2-digit port\n"
            "• Use `&pcreate <vps_id> <port>` for a specific port\n"
            "• Prefix: `&`"
        ),
        inline=False
    )

    embed.set_footer(text="Nova • Cloud Services")

    await ctx.send(embed=embed)


# ─── Scrollable Help System ────────────────────────────────────────────────────

def build_help_pages(is_user_admin, is_user_main_admin):
    pages = {}

    # Page 2: User Commands
    user_embed = create_embed("👤 User Commands", "VPS management commands for all users:", 0x00ff88)
    user_embed.add_field(name="🖥️ VPS Management", value=(
        "`&manage` — Manage your VPS (Start/Stop/SSH/Reinstall)\n"
        "`&manage [@user]` — Admin: manage another user's VPS\n"
        "`&share-user @user <#>` — Share VPS access\n"
        "`&share-ruser @user <#>` — Revoke shared access\n"
        "`&manage-shared @owner <#>` — Access shared VPS"
    ), inline=False)
    user_embed.add_field(name="🏷️ VPS Tools", value=(
        "`&rename-vps <#> <new_name>` — Give your VPS a nickname\n"
        "`&vps-note <#> <note>` — Add a note to your VPS\n"
        "`&ping-vps <#>` — Ping your VPS container\n"
        "`&uptime-vps <#>` — Check VPS uptime\n"
        "`&myinfo` — View your profile & VPS summary"
    ), inline=False)
    user_embed.add_field(
        name="🔌 Port Forwarding",
        value=(
            "`&ports` — Port forwarding help\n"
            "`&pcreate <vps_id> <port>` — Create a port forward\n"
            "`&pcreate <vps_id>` — Create random 2-digit port\n"
            "Port `22` — Reserved for SSH/Tailscale"
        ),
        inline=False
    )

    port_embed = create_embed(
        "📚 Nova Help - 🔌 Port Forwarding",
        "Manage port forwarding for your VPS",
        0x00ccff
    )

    port_embed.add_field(
        name="⌯⌲ Commands",
        value=(
            "**`&ports`** - Port forwarding help\n"
            "**`&pcreate <vps_num> <port>`** - Add port forward\n"
            "**`&pcreate <vps_num>`** - Random 2–4 digit port\n"
            "**`&ports list`** - List your port forwards\n"
            "**`&ports remove <id>`** - Remove port forward"
        ),
        inline=False
    )

    port_embed.add_field(
        name="🔐 SSH / Tailscale",
        value=(
            "**Port `22`** is reserved for SSH/Tailscale.\n"
            "Example: `&pcreate 1 22`"
        ),
        inline=False
    )

    port_embed.add_field(
        name="⌯⌲ Navigation",
        value=(
            "• Use dropdown to switch categories\n"
            "• Select one VPS using its number\n"
            "• Random ports are `10–9999`\n"
            "• Prefix: `&`"
        ),
        inline=False
    )

    port_embed.set_footer(text="NovaNode • Cloud Services")
    pages["ports"] = port_embed

    pages["user"] = user_embed

    # Page 3: Credits & Plans
    credits_embed = create_embed("💰 Credits & Plans", "Purchase plans and manage credits:", 0xffaa00)
    credits_embed.add_field(name="💳 Commands", value=(
        "`&plans` — View available VPS plans & prices\n"
        "`&buyc` — Get payment info (UPI)\n"
        "`&buywc <plan> <Intel/AMD>` — Buy VPS with credits\n"
        "`&bal [@user]` — Check your balance or another user\n"
        "`&baltop` — Show the top coin holders\n"
        "`&give @user <coins>` — Admin-only: add coins\n"
        "`&transfer @user <amount>` — Send credits to another user"
    ), inline=False)
    plans_text = "\n".join(
        f"**{name.title()}** — {plan['ram']}GB RAM | {plan['cpu']} CPU | {plan['disk']}GB disk | "
        + ", ".join(f"{processor}: {price}cr" for processor, price in plan.get("credits", {}).items())
        for name, plan in VPS_PLANS.items()
    ) or "No purchase plans are configured."
    credits_embed.add_field(name="📦 Available Plans", value=plans_text, inline=False)
    pages["credits"] = credits_embed

    # Page 4: VPS Tools
    tools_embed = create_embed("🔧 VPS Tools", "Extra tools to manage & monitor your VPS:", 0x00ccff)
    tools_embed.add_field(name="📊 Monitoring", value=(
        "`&ping-vps <#>` — Ping VPS container (check if alive)\n"
        "`&uptime-vps <#>` — Show how long VPS has been running\n"
        "`&myinfo` — Your full profile: credits, VPS list, notes"
    ), inline=False)
    tools_embed.add_field(name="🏷️ Customization", value=(
        "`&rename-vps <#> <name>` — Set a nickname for your VPS\n"
        "`&vps-note <#> <text>` — Add/update a note on your VPS\n"
        "  e.g. `&vps-note 1 My Minecraft server`"
    ), inline=False)
    tools_embed.add_field(name="💸 Economy", value=(
        "`&transfer @user <amount>` — Send credits to a friend\n"
        "`&leaderboard` — Top 10 credit holders\n"
        "`&botstatus` — Show bot stats & uptime"
    ), inline=False)
    pages["tools"] = tools_embed

    # Page 5: Extras
    extras_embed = create_embed("📢 Extras & Fun", "Announcements, leaderboard, and more:", 0xff6b9d)
    extras_embed.add_field(name="📣 Announcements", value=(
        "`&announce <message>` — Admin: broadcast to all VPS owners via DM\n"
        "`&botstatus` — Bot uptime, total VPS, active users\n"
        "`&leaderboard` — Top credit holders on the server"
    ), inline=False)
    extras_embed.add_field(name="ℹ️ Info", value=(
        "`&help` — Open this help menu\n"
        "`&plans` — VPS plan pricing\n"
        "`&myinfo` — Your personal dashboard"
    ), inline=False)
    pages["extras"] = extras_embed

    # Page 6: Admin Panel
    if is_user_admin:
        admin_embed = create_embed("🛡️ Admin Panel", "Admin-only VPS management commands:", 0xff3366)
        admin_embed.add_field(name="🖥️ VPS Control", value=(
            f"`&deploy @user` — Quick VPS ({ADMIN_DEPLOY_PLAN['ram']}GB / {ADMIN_DEPLOY_PLAN['cpu']} core / {ADMIN_DEPLOY_PLAN['disk']}GB)\n"
            "`&create @user <ram_GB> <cpu_cores> <storage_GB>` — Custom VPS (Mode → OS selection)\n"
            "`&delete-vps @user <#> <reason>` — Delete a user's VPS\n"
            "`&restart-vps <container>` — Restart a VPS container\n"
            "`&stop-vps-all` — Emergency stop ALL VPS\n"
            "`&exec <container> <cmd>` — Run command inside VPS"
        ), inline=False)
        admin_embed.add_field(name="💾 Backup & Restore", value=(
            "`&backup-vps <container>` — Create Docker image snapshot\n"
            "`&restore-vps <container> <snapshot>` — Restore from snapshot\n"
            "`&list-snapshots <container>` — List all snapshots"
        ), inline=False)
        admin_embed.add_field(name="📊 Info & Economy", value=(
            "`&userinfo @user` — Full user info + VPS list\n"
            "`&serverstats` — Server overview stats\n"
            "`&vpsinfo [container]` — VPS details\n"
            "`&list-all` — All VPS overview\n"
            "`&adminc @user <amount>` — Add credits\n"
            "`&adminrc @user <amount/all>` — Remove credits\n"
            "`&announce <msg>` — DM all VPS owners\n"
            "`&msg select chenel #channel msg <message>` — Send exact message to a channel\n"
            "`&cpu-monitor <status|enable|disable>` — CPU monitor control\n"
            "`&maintenance <on/off>` — Toggle maintenance mode"
        ), inline=False)
        admin_embed.add_field(name="⏳ Expire Management", value=(
            "`&setexpire @user <vps#> <days>` — Set VPS expiry\n"
            "`&extendexpire @user <vps#> <days>` — Extend expiry\n"
            "`&removeexpire @user <vps#>` — Remove expiry (set to Never)\n"
            "`&checkexpire [@user]` — Check expiry status"
        ), inline=False)
        pages["admin"] = admin_embed

    # Page 7: Main Admin
    if is_user_main_admin:
        mainadmin_embed = create_embed("👑 Main Admin", "Exclusive main admin commands:", 0xffd700)
        mainadmin_embed.add_field(name="👥 Admin Management", value=(
            "`&admin-add @user` — Promote user to admin\n"
            "`&admin-remove @user` — Remove admin role\n"
            "`&admin-list` — View all admins"
        ), inline=False)
        pages["mainadmin"] = mainadmin_embed

    return pages


@bot.command(name='help')
async def show_help(ctx):
    """Show scrollable categorized help"""
    user_id = str(ctx.author.id)
    is_user_admin = _is_owner(user_id) or user_id in admin_data.get("admins", [])
    is_user_main_admin = _is_owner(user_id)

    pages = build_help_pages(is_user_admin, is_user_main_admin)

    options = [
        discord.SelectOption(label="👤 User Commands", description="VPS manage, share, tools", value="user", emoji="👤"),
        discord.SelectOption(label="🔌 Port Forwarding", description="Manage VPS ports", value="ports", emoji="🔌"),
        discord.SelectOption(label="💰 Credits & Plans", description="Buy VPS, check plans", value="credits", emoji="💰"),
        discord.SelectOption(label="🔧 VPS Tools", description="Rename, notes, ping, uptime", value="tools", emoji="🔧"),
        discord.SelectOption(label="📢 Extras", description="Announcements, leaderboard", value="extras", emoji="📢"),
    ]
    if is_user_admin:
        options.append(discord.SelectOption(label="🛡️ Admin Panel", description="Admin VPS commands", value="admin", emoji="🛡️"))
    if is_user_main_admin:
        options.append(discord.SelectOption(label="👑 Main Admin", description="Admin management", value="mainadmin", emoji="👑"))

    class HelpSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="📂 Select a category...", options=options, min_values=1, max_values=1)

        async def callback(self, interaction: discord.Interaction):
            selected = self.values[0]
            embed = pages.get(selected, pages["user"])
            await interaction.response.edit_message(embed=embed, view=self.view)

    class HelpView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=180)
            self.add_item(HelpSelect())

    await ctx.send(embed=pages["user"], view=HelpView())



# ─── Admin Channel Message ────────────────────────────────────────────────────

@bot.command(name='msg')
@is_admin()
async def admin_channel_message(ctx, *, raw: str = ""):
    """
    Admin-only channel message command.

    Usage:
    &msg select chenel #channel msg Your message
    """

    # Expected:
    # select chenel #channel msg <message>

    parts = raw.split(maxsplit=3)

    if len(parts) < 4:
        await ctx.send(
            embed=create_error_embed(
                "❌ Invalid Command",
                "Use:\n`&msg select chenel #channel msg Your message`"
            ),
            delete_after=8
        )
        return

    action = parts[0].lower()
    channel_word = parts[1].lower()
    channel_mention = parts[2]
    message_part = parts[3]

    if action != "select" or channel_word not in ("chenel", "channel"):
        await ctx.send(
            embed=create_error_embed(
                "❌ Invalid Format",
                "Use:\n`&msg select chenel #channel msg Your message`"
            ),
            delete_after=8
        )
        return

    if not message_part.lower().startswith("msg "):
        await ctx.send(
            embed=create_error_embed(
                "❌ Missing Message",
                "Use:\n`&msg select chenel #channel msg Your message`"
            ),
            delete_after=8
        )
        return

    # IMPORTANT:
    # Everything after "msg " is preserved exactly as entered.
    message_text = message_part[4:]

    if not message_text:
        await ctx.send(
            embed=create_error_embed(
                "❌ Empty Message",
                "Please write a message after `msg`."
            ),
            delete_after=8
        )
        return

    # Resolve #channel mention safely
    channel = None

    if channel_mention.startswith("<#") and channel_mention.endswith(">"):
        try:
            channel_id = int(channel_mention[2:-1])
            channel = ctx.guild.get_channel(channel_id)
        except ValueError:
            channel = None

    # Fallback: try channel name
    if channel is None:
        clean_name = channel_mention.lstrip("#")
        channel = discord.utils.get(
            ctx.guild.text_channels,
            name=clean_name
        )

    if channel is None:
        await ctx.send(
            embed=create_error_embed(
                "❌ Channel Not Found",
                "Please select a valid server text channel."
            ),
            delete_after=8
        )
        return

    if not isinstance(channel, discord.TextChannel):
        await ctx.send(
            embed=create_error_embed(
                "❌ Invalid Channel",
                "Please select a text channel."
            ),
            delete_after=8
        )
        return

    try:
        # Send EXACTLY what admin typed after "msg "
        await channel.send(
            message_text,
            allowed_mentions=discord.AllowedMentions.all()
        )

        # Delete the admin command so it doesn't clutter the channel
        try:
            await ctx.message.delete()
        except Exception:
            pass

        # Confirmation in the command channel
        await ctx.send(
            embed=create_success_embed(
                "📢 Message Sent",
                f"Message successfully sent to {channel.mention}."
            ),
            delete_after=5
        )

    except discord.Forbidden:
        await ctx.send(
            embed=create_error_embed(
                "❌ Permission Denied",
                f"I don't have permission to send messages in {channel.mention}."
            )
        )

    except Exception as e:
        await ctx.send(
            embed=create_error_embed(
                "❌ Message Failed",
                str(e)[:1000]
            )
        )


# ─── New Cool Features ──────────────────────────────────────────────────────────

BOT_START_TIME = datetime.now()
maintenance_mode = False

@bot.command(name='rename-vps')
async def rename_vps(ctx, vps_number: int, *, new_name: str):
    """Give your VPS a custom nickname"""
    user_id = str(ctx.author.id)
    vps_list = vps_data.get(user_id, [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    if len(new_name) > 30:
        await ctx.send(embed=create_error_embed("Name Too Long", "Nickname must be 30 characters or less."))
        return
    vps_list[vps_number - 1]["nickname"] = new_name
    save_data()
    await ctx.send(embed=create_success_embed("VPS Renamed", f"VPS #{vps_number} is now called **{new_name}**!"))


@bot.command(name='vps-note')
async def vps_note(ctx, vps_number: int, *, note: str):
    """Add a note to your VPS"""
    user_id = str(ctx.author.id)
    vps_list = vps_data.get(user_id, [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    vps_list[vps_number - 1]["note"] = note[:200]
    save_data()
    await ctx.send(embed=create_success_embed("Note Saved", f"Note added to VPS #{vps_number}:\n> {note[:200]}"))


@bot.command(name='ping-vps')
async def ping_vps(ctx, vps_number: int):
    """Ping your VPS container to check if it's alive"""
    user_id = str(ctx.author.id)
    vps_list = vps_data.get(user_id, [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    vps = vps_list[vps_number - 1]
    container = vps["container_name"]
    nickname = vps.get("nickname", f"VPS #{vps_number}")
    msg = await ctx.send(embed=create_info_embed("Pinging...", f"Checking `{container}`..."))
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format={{.State.Running}}", container,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        elapsed = int((time.time() - start) * 1000)
        is_running = stdout.decode().strip() == "true"
        if is_running:
            embed = create_success_embed("🏓 Pong!", f"**{nickname}** is alive!\n⚡ Response: `{elapsed}ms`")
        else:
            embed = create_error_embed("💀 No Response", f"**{nickname}** container is not running.")
        await msg.edit(embed=embed)
    except Exception as e:
        await msg.edit(embed=create_error_embed("Ping Failed", str(e)))


@bot.command(name='uptime-vps')
async def uptime_vps(ctx, vps_number: int):
    """Check VPS uptime"""
    user_id = str(ctx.author.id)
    vps_list = vps_data.get(user_id, [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    vps = vps_list[vps_number - 1]
    container = vps["container_name"]
    nickname = vps.get("nickname", f"VPS #{vps_number}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format={{.State.StartedAt}}", container,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        started_at_str = stdout.decode().strip()
        # Parse docker time format
        started_at = datetime.fromisoformat(started_at_str[:19])
        uptime_delta = datetime.utcnow() - started_at
        days = uptime_delta.days
        hours, rem = divmod(uptime_delta.seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
        embed = create_success_embed(f"⏱️ Uptime — {nickname}", f"Container has been running for:\n```{uptime_str}```")
        embed.add_field(name="Started At", value=f"`{started_at_str[:19]} UTC`", inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Uptime Error", str(e)))


@bot.command(name='myinfo')
async def my_info(ctx):
    """Show your personal dashboard"""
    user_id = str(ctx.author.id)
    credits = user_data.get(user_id, {}).get("credits", 0)
    vps_list = vps_data.get(user_id, [])
    embed = create_embed(f"👤 {ctx.author.name}'s Dashboard", "", 0x5865F2)
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1530142266281164871/1560262829657821236/fc27d6b324431b73e38c600ecb538f09.gif?ex=6ad2137e&is=6ad0c1fe&hm=733aae9ef1edbfa9c8ecf47593040ebbb24eae8d81a866563e85fe1102ba24ed&")
    embed.add_field(name="💰 Credits", value=f"**{credits}**", inline=True)
    embed.add_field(name="🖥️ VPS Count", value=f"**{len(vps_list)}**", inline=True)
    embed.add_field(name="📅 Account", value=f"Joined: {ctx.author.created_at.strftime('%Y-%m-%d')}", inline=True)
    if vps_list:
        vps_text = ""
        for i, v in enumerate(vps_list):
            nickname = v.get("nickname", f"VPS {i+1}")
            status_icon = "🟢" if v.get("status") == "running" else "🔴"
            note = f" — _{v['note']}_" if v.get("note") else ""
            vps_text += f"{status_icon} **{nickname}** (`{v['container_name']}`){note}\n"
        embed.add_field(name="🖥️ Your VPS", value=vps_text, inline=False)
    else:
        embed.add_field(name="🖥️ Your VPS", value="No VPS yet. Use `&buywc` to get one!", inline=False)
    await ctx.send(embed=embed)


@bot.command(name='transfer')
async def transfer_credits(ctx, target: discord.Member, amount: int):
    """Transfer credits to another user"""
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Amount must be positive."))
        return
    if target.id == ctx.author.id:
        await ctx.send(embed=create_error_embed("Invalid Target", "You can't transfer to yourself!"))
        return
    sender_id = str(ctx.author.id)
    target_id = str(target.id)
    if sender_id not in user_data:
        user_data[sender_id] = {"credits": 0}
    if user_data[sender_id]["credits"] < amount:
        await ctx.send(embed=create_error_embed("Insufficient Credits",
            f"You only have **{user_data[sender_id]['credits']}** credits."))
        return
    if target_id not in user_data:
        user_data[target_id] = {"credits": 0}
    user_data[sender_id]["credits"] -= amount
    user_data[target_id]["credits"] += amount
    save_data()
    embed = create_success_embed("💸 Transfer Complete",
        f"{ctx.author.mention} sent **{amount}** credits to {target.mention}!")
    embed.add_field(name="Your Balance", value=f"**{user_data[sender_id]['credits']}** credits", inline=True)
    await ctx.send(embed=embed)
    try:
        await target.send(embed=create_info_embed("💰 Credits Received",
            f"You received **{amount}** credits from {ctx.author.mention}!\nNew balance: **{user_data[target_id]['credits']}**"))
    except discord.Forbidden:
        pass


@bot.command(name='baltop', aliases=['leaderboard'])
async def leaderboard(ctx):
    """Show top 10 credit holders"""
    sorted_users = sorted(user_data.items(), key=lambda x: x[1].get("credits", 0), reverse=True)[:10]
    embed = create_embed("🏆 Credit Leaderboard", "Top 10 credit holders:", 0xffd700)
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = []
    for i, (uid, data) in enumerate(sorted_users):
        try:
            u = await bot.fetch_user(int(uid))
            name = u.name
        except:
            name = f"User#{uid[:4]}"
        lines.append(f"{medals[i]} **{name}** — {data.get('credits', 0)} credits")
    embed.add_field(name="Rankings", value="\n".join(lines) if lines else "No data yet.", inline=False)
    await ctx.send(embed=embed)


@bot.command(name='botstatus')
async def bot_status(ctx):
    """Show bot status and stats"""
    uptime_delta = datetime.now() - BOT_START_TIME
    days = uptime_delta.days
    hours, rem = divmod(uptime_delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    total_vps = sum(len(v) for v in vps_data.values())
    running_vps = sum(1 for vl in vps_data.values() for v in vl if v.get("status") == "running")
    embed = create_embed("🤖 Bot Status", "Nova VPS Manager", 0x00ff88)
    embed.add_field(name="⏱️ Uptime", value=f"`{days}d {hours}h {minutes}m`", inline=True)
    embed.add_field(name="🖥️ Total VPS", value=f"**{total_vps}** ({running_vps} running)", inline=True)
    embed.add_field(name="👥 Users", value=f"**{len(user_data)}**", inline=True)
    embed.add_field(name="🔧 Maintenance", value="🔴 ON" if maintenance_mode else "🟢 OFF", inline=True)
    embed.add_field(name="📡 Latency", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    await ctx.send(embed=embed)


@bot.command(name='announce')
@is_admin()
async def announce(ctx, *, message: str):
    """Send an announcement DM to all VPS owners (Admin only)"""
    sent = 0
    failed = 0
    announce_embed = create_embed("📢 Announcement", message, 0xffaa00)
    announce_embed.add_field(name="From", value=f"**NovaNode Team** ({ctx.author.mention})", inline=False)
    status_msg = await ctx.send(embed=create_info_embed("Sending Announcement", "Broadcasting to all VPS owners..."))
    for uid in vps_data.keys():
        try:
            user = await bot.fetch_user(int(uid))
            await user.send(embed=announce_embed)
            sent += 1
            await asyncio.sleep(0.5)  # Rate limit protection
        except:
            failed += 1
    await status_msg.edit(embed=create_success_embed("Announcement Sent",
        f"✅ Delivered to **{sent}** users\n❌ Failed: **{failed}** (DMs closed)"))


@bot.command(name='maintenance')
@is_admin()
async def maintenance_toggle(ctx, mode: str):
    """Toggle maintenance mode (Admin only)"""
    global maintenance_mode
    if mode.lower() == "on":
        maintenance_mode = True
        # Keep the bot's status red (DND) during maintenance.
        await bot.change_presence(
            status=discord.Status.dnd,
            activity=discord.Activity(type=discord.ActivityType.watching, name="🔴 Under Maintenance")
        )
        await ctx.send(embed=create_warning_embed("🔴 Maintenance Mode ON",
            "Bot is now in maintenance mode.\n"
            "• ALL commands blocked for non-admins\n"
            "• DM commands also blocked\n"
            "• Bot status set to Idle"))
    elif mode.lower() == "off":
        maintenance_mode = False
        # Restore normal red (DND) status.
        await bot.change_presence(
            status=discord.Status.dnd,
            activity=discord.Activity(type=discord.ActivityType.watching, name="NovaNode | VPS Manager")
        )
        await ctx.send(embed=create_success_embed("🟢 Maintenance Mode OFF",
            "Bot is back to normal operation."))
    else:
        await ctx.send(embed=create_error_embed("Invalid", "Use: `&maintenance on` or `&maintenance off`"))


# ─── Maintenance mode global check ────────────────────────────────────────────
@bot.check
async def maintenance_check(ctx):
    global maintenance_mode
    if not maintenance_mode:
        return True

    user_id = str(ctx.author.id)
    is_user_admin = _is_owner(user_id) or user_id in admin_data.get("admins", [])

    # Admins can still use maintenance toggle itself
    if is_user_admin and ctx.command and ctx.command.name == 'maintenance':
        return True

    # Block EVERYONE else — no help, no botstatus, nothing
    if not is_user_admin:
        await ctx.send(embed=create_warning_embed(
            "🔴 Under Maintenance",
            "The bot is currently under maintenance.\n"
            "All commands are disabled until maintenance is complete."
        ))
        return False

    return True


# ─── Block ALL bot commands in DMs — server only ──────────────────────────────
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Block ALL commands in DMs for everyone (including admins for normal usage)
    if isinstance(message.channel, discord.DMChannel):
        if message.content.startswith(bot.command_prefix):
            block_embed = create_warning_embed(
                "❌ DM Commands Disabled",
                "Bot commands are **not allowed in DMs**.\n\n"
                "Please use bot commands in the **server channel** only.\n"
                "👉 Go to the server and use commands there!"
            )
            block_embed.set_footer(text="NovaNode | Server-only bot")
            await message.channel.send(embed=block_embed)
            return  # Never process DM commands

    # If maintenance mode ON — block non-admins in server too
    if maintenance_mode and isinstance(message.channel, discord.TextChannel):
        user_id = str(message.author.id)
        is_user_admin = _is_owner(user_id) or user_id in admin_data.get("admins", [])
        if not is_user_admin and message.content.startswith(bot.command_prefix):
            await message.channel.send(embed=create_warning_embed(
                "🔴 Under Maintenance",
                "The bot is currently under maintenance.\nAll commands are disabled for users."
            ))
            return

    # ─── NovaNode_AI_PING_HANDLER ────────────────────────────────────────────
    #
    # AI works ONLY when a user mentions the bot.
    # This does NOT create !ai and does NOT interfere with existing commands.

    if (
        message.guild is not None
        and bot.user is not None
        and bot.user in message.mentions
        and not message.author.bot
    ):
        try:
            # Remove the bot mention from the message.
            question = message.content

            question = question.replace(
                f"<@{bot.user.id}>",
                ""
            )

            question = question.replace(
                f"<@!{bot.user.id}>",
                ""
            )

            question = question.strip()

            if question:

                # Built-in NovaNode identity responses
                direct = NovaNode_ai_direct_answer(question)

                if direct:
                    answer = direct
                else:
                    if not OPENROUTER_API_KEY:
                        answer = (
                            "🤖 Nova AI is currently not configured.\n"
                            "Please contact the Nova administrator."
                        )
                    else:
                        async with message.channel.typing():
                            answer = await NovaNode_ai_answer(question)

                # Discord message limit
                if len(answer) <= 3900:
                    await message.reply(
                        answer,
                        mention_author=False
                    )
                else:
                    for start in range(0, len(answer), 3900):
                        await message.channel.send(
                            answer[start:start + 3900]
                        )

        except Exception as ai_error:
            logger.error(
                f"NovaNode AI error: {ai_error}"
            )

            await message.reply(
                "🤖 AI request failed. Please try again.",
                mention_author=False
            )

    # Existing command system stays untouched.
    await bot.process_commands(message)


# Typo aliases
@bot.command(name='mangage')
async def manage_typo(ctx):
    await ctx.send(embed=create_info_embed("Command Correction", "Did you mean `&manage`?"))

@bot.command(name='stats')
async def stats_alias(ctx):
    if _is_owner(ctx.author.id) or str(ctx.author.id) in admin_data.get("admins", []):
        await server_stats(ctx)
    else:
        await ctx.send(embed=create_error_embed("Access Denied", "Admin only."))


# ─── Expire System ─────────────────────────────────────────────────────────────

@bot.command(name='setexpire')
@is_admin()
async def set_expire(ctx, user: discord.Member, vps_number: int, days: int):
    """Set VPS expiry — 1setexpire @user <vps#> <days>"""
    user_id = str(user.id)
    vps_list = vps_data.get(user_id, [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        await ctx.send(embed=create_error_embed("Not Found", f"{user.mention} has no VPS #{vps_number}."))
        return
    import datetime as dt
    exp_date = (datetime.utcnow() + dt.timedelta(days=days)).isoformat()
    vps_list[vps_number - 1]['expires'] = exp_date
    save_data()
    vps_name = vps_list[vps_number - 1]['container_name']
    await ctx.send(embed=create_success_embed("Expiry Set",
        f"✅ {user.mention}'s **VPS #{vps_number}** (`{vps_name}`) expires on `{exp_date[:10]}` ({days}d from now)."))
    try:
        await user.send(embed=create_warning_embed("⏳ VPS Expiry Set",
            f"Your **VPS #{vps_number}** (`{vps_name}`) has been set to expire on **{exp_date[:10]}** ({days} days).\n"
            f"Contact an admin to extend it before it expires!"))
    except discord.Forbidden:
        pass


@bot.command(name='extendexpire')
@is_admin()
async def extend_expire(ctx, user: discord.Member, vps_number: int, days: int):
    """Extend VPS expiry — 1extendexpire @user <vps#> <days>"""
    user_id = str(user.id)
    vps_list = vps_data.get(user_id, [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        await ctx.send(embed=create_error_embed("Not Found", f"{user.mention} has no VPS #{vps_number}."))
        return
    import datetime as dt
    vps = vps_list[vps_number - 1]
    current = vps.get('expires', 'Never')
    if current == 'Never' or not current:
        base = datetime.utcnow()
    else:
        try:
            base = datetime.fromisoformat(current)
            if base < datetime.utcnow():
                base = datetime.utcnow()
        except:
            base = datetime.utcnow()
    new_exp = (base + dt.timedelta(days=days)).isoformat()
    vps['expires'] = new_exp
    save_data()
    vps_name = vps['container_name']
    await ctx.send(embed=create_success_embed("Expiry Extended",
        f"✅ {user.mention}'s **VPS #{vps_number}** (`{vps_name}`) extended by **{days} days**.\nNew expiry: `{new_exp[:10]}`"))
    try:
        await user.send(embed=create_success_embed("✅ VPS Extended",
            f"Your **VPS #{vps_number}** (`{vps_name}`) has been extended by **{days} days**!\nNew expiry: **{new_exp[:10]}**"))
    except discord.Forbidden:
        pass


@bot.command(name='checkexpire')
async def check_expire(ctx, user: discord.Member = None):
    """Check VPS expiry — users check own, admins can check others"""
    is_user_admin = _is_owner(ctx.author.id) or str(ctx.author.id) in admin_data.get("admins", [])
    target = user if (user and is_user_admin) else ctx.author
    user_id = str(target.id)
    if user_id not in vps_data or not vps_data[user_id]:
        await ctx.send(embed=create_error_embed("Not Found", f"{target.mention} has no VPS."))
        return
    embed = create_info_embed(f"⏳ VPS Expiry — {target.display_name}", "")
    for i, vps in enumerate(vps_data[user_id]):
        expires = vps.get('expires', 'Never')
        if expires and expires != 'Never':
            try:
                exp_dt = datetime.fromisoformat(expires)
                days_left = (exp_dt - datetime.utcnow()).days
                if days_left < 0:
                    status = f"❌ **EXPIRED** {abs(days_left)}d ago"
                elif days_left <= 3:
                    status = f"⚠️ Expires in **{days_left}d** — {expires[:10]}"
                else:
                    status = f"✅ Expires on `{expires[:10]}` ({days_left}d left)"
            except:
                status = expires
        else:
            status = "♾️ Never (No expiry set)"
        embed.add_field(
            name=f"VPS #{i+1} — `{vps['container_name']}`",
            value=status, inline=False
        )
    await ctx.send(embed=embed)


@bot.command(name='removeexpire')
@is_admin()
async def remove_expire(ctx, user: discord.Member, vps_number: int):
    """Remove expiry from a specific VPS — 1removeexpire @user <vps#>"""
    user_id = str(user.id)
    vps_list = vps_data.get(user_id, [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        await ctx.send(embed=create_error_embed("Not Found", f"{user.mention} has no VPS #{vps_number}."))
        return
    vps_list[vps_number - 1]['expires'] = 'Never'
    save_data()
    vps_name = vps_list[vps_number - 1]['container_name']
    await ctx.send(embed=create_success_embed("Expiry Removed",
        f"✅ {user.mention}'s **VPS #{vps_number}** (`{vps_name}`) expiry set to **Never**."))


# Auto expire checker — runs every hour
@tasks.loop(hours=1)
async def auto_expire_check():
    now = datetime.utcnow()
    to_delete = []
    for user_id, vps_list in list(vps_data.items()):
        for vps in vps_list:
            expires = vps.get('expires', 'Never')
            if not expires or expires == 'Never':
                continue
            try:
                exp_dt = datetime.fromisoformat(expires)
                days_left = (exp_dt - now).days
                # Warn at 3 days
                if days_left == 3:
                    try:
                        u = await bot.fetch_user(int(user_id))
                        await u.send(embed=create_warning_embed(
                            "⚠️ VPS Expiring Soon",
                            f"Your VPS `{vps['container_name']}` expires in **3 days** on `{expires[:10]}`1\n"
                            "Contact an admin to extend it."
                        ))
                    except:
                        pass
                # Warn at 1 day
                elif days_left == 1:
                    try:
                        u = await bot.fetch_user(int(user_id))
                        await u.send(embed=create_error_embed(
                            "🚨 VPS Expiring Tomorrow!",
                            f"Your VPS `{vps['container_name']}` expires **tomorrow** (`{expires[:10]}`)!\n"
                            "Contact an admin IMMEDIATELY to avoid losing access."
                        ))
                    except:
                        pass
                # Expired — stop container
                elif days_left < 0:
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "docker", "stop", vps['container_name'],
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                        )
                        await proc.communicate()
                        vps['status'] = 'stopped'
                    except:
                        pass
                    try:
                        u = await bot.fetch_user(int(user_id))
                        await u.send(embed=create_error_embed(
                            "❌ VPS Expired",
                            f"Your VPS `{vps['container_name']}` has expired and been **stopped**.\n"
                            "Contact an admin to renew it."
                        ))
                    except:
                        pass
            except:
                continue
    save_data()


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing bot token. Run install_sift.sh (or put DISCORD_TOKEN=... in sift.env).")
    bot.run(token)


async def _NovaNode_sync_commands():
    try:
        synced = await bot.tree.sync()
        print(f"[NovaNode] Synced {len(synced)} slash commands", flush=True)
    except Exception as e:
        print(f"[NovaNode] Slash sync error: {e}", flush=True)

bot.add_listener(_NovaNode_sync_commands, 'on_ready')
