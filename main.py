import asyncio
import colorama
import telnetlib3
import json
import re
import sys
from colorama import init, Fore, Style

init(autoreset=True)

CONFIG_FILE = "config.json"
smallwelcomeart=r"""
 _   _                           _               
| \ | |                         | |              
|  \| | ___  ___  _ __ ___  __ _| |_ __ ___  ___ 
| . ` |/ _ \/ _ \| '__/ _ \/ _` | | '_ ` _ \/ __|
| |\  |  __/ (_) | | |  __/ (_| | | | | | | \__ \
\_| \_/\___|\___/|_|  \___|\__,_|_|_| |_| |_|___/
"""


def color_send(msg):
    return f"{Fore.RED}[nr]{Style.RESET_ALL} {Fore.WHITE}{msg}{Style.RESET_ALL}"

def parse_aliases(alias_list):
    aliases = {}
    for entry in alias_list:
        if "|" in entry:
            key, value = entry.split("|", 1)
            aliases[key.strip()] = value.strip()
    return aliases

def detect_new_room(text):
    # Looks for a room name (first line), description (second line), and exits
    lines = text.strip().splitlines()
    if len(lines) < 3:
        return None
    room_name = lines[0].strip()
    # Basic check: first line is title case, and "exit" in 3rd or 4th line
    if re.match(r'^[A-Z][A-Za-z0-9 \-]+$', room_name) and any("exit" in l.lower() for l in lines[2:4]):
        return room_name
    return None

def find_money_in_room(text):
    match = re.search(r'You see: \$([0-9]+)', text)
    if match:
        return match.group(1)
    return None

def find_money_drop(text):
    match = re.search(r'drops \$([0-9]+)\.', text)
    if match:
        return match.group(1)
    return None

def normalize_location(name):
    return name.lower().replace(" ", "")

def update_account_location(config, username, location):
    """Update last_seen_location for the account in config and save."""
    for acc in config['info'].get('accounts', []):
        if acc['username'] == username:
            acc['last_seen_location'] = normalize_location(location)
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            break

def find_money_ground_drop(text):
    # Looks for "$<amount> drops to the ground."
    match = re.search(r'\$([0-9]+) drops to the ground\.', text)
    if match:
        return match.group(1)
    return None

async def read_stdin(queue):
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, input, "> ")
        await queue.put(line)

async def choose_account(config):
    info = config['info']
    accounts = info.get('accounts', [])
    print(f"Host: {info['host']}  Port: {info['port']}")
    print(f"Main account: {info['username']}")
    if accounts:
        print("Alternate accounts:")
        for idx, acc in enumerate(accounts, 1):
            loc = acc.get("last_seen_location", "unknown")
            print(f"  {idx}: {acc['username']} (last seen: {loc})")
        choice = input("Use alternate account? (y/n): ").strip().lower()
        if choice == "y":
            while True:
                idx = input(f"Select account [1-{len(accounts)}]: ").strip()
                if idx.isdigit() and 1 <= int(idx) <= len(accounts):
                    acc = accounts[int(idx)-1]
                    return acc['username'], acc['password'], int(idx)-1
                print("Invalid selection.")
        elif choice == "quit" or choice == "exit":
            exit(0)
    # Default to main account
    return info['username'], info['password'], None

async def attack_loop(writer, attack_speed, stop_event):
    while not stop_event.is_set():
        print(color_send("attack"))
        writer.write("attack\r\n")
        await asyncio.sleep(attack_speed)

def has_enemies(text):
    # Looks for "Enemies: ..." with at least one enemy listed
    m = re.search(r'Enemies:\s*(.+)', text)
    if m:
        enemies = m.group(1).strip()
        return bool(enemies) and enemies.lower() != "none"
    return False

async def main():
    print(colorama.Fore.RED + f"{smallwelcomeart}{colorama.Style.RESET_ALL}")
    # Load configuration
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    info = config['info']

    # Account selection, login, etc. as before
    username, password, acc_index = await choose_account(config)
    aliases = parse_aliases(info.get('aliases', []))
    aaf_dont_take_money = info.get('aaf_dont_take_money', False)
    host = info['host']
    port = info['port']
    attack_speed = float(info.get('attack_speed', 5))

    reader, writer = await telnetlib3.open_connection(host, port)
    input_queue = asyncio.Queue()
    asyncio.create_task(read_stdin(input_queue))

    # --- LOGIN ---
    await asyncio.sleep(0.5)
    writer.write(username + "\r\n")
    await asyncio.sleep(0.5)
    writer.write(password + "\r\n")

    last_command = ""
    json_mode = False
    json_buffer = ""
    last_room = None

    # Attack state
    attack_task = None
    attack_stop_event = asyncio.Event()
    print(f"\n{Fore.GREEN}Connected to {host}:{port} as {username}.{Style.RESET_ALL} Type commands or !quit to exit.")

    while True:
        try:
            cmd = await asyncio.wait_for(input_queue.get(), timeout=0.1)
            if cmd == "!quit":
                break
            if not cmd:
                cmd = last_command
            elif cmd != "api":
                last_command = cmd
            if cmd.startswith('!') and cmd[1:] in aliases:
                cmd = aliases[cmd[1:]]
            if cmd == "api":
                json_mode = True
                json_buffer = ""
            print(color_send(cmd))
            writer.write(cmd + "\r\n")
        except asyncio.TimeoutError:
            pass

        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=0.1)
            if not data:
                break

            filtered = re.sub(r'\{.*?\}', '', data, flags=re.DOTALL)

            if json_mode:
                json_buffer += filtered
                if re.search(r'}\s*$', json_buffer):
                    try:
                        parsed = json.loads(json_buffer)
                        print("\nAPI Response:", json.dumps(parsed, indent=2))
                    except json.JSONDecodeError:
                        print("\nInvalid JSON response")
                    json_mode = False
                    json_buffer = ""
            else:
                # --- Monster logic ---
                # 1. Monster enters the room!
                if "Monster enters the room!" in filtered:
                    if attack_task is None or attack_task.done():
                        print(f"{Fore.MAGENTA}[Monster detected! Auto-attacking...]{Style.RESET_ALL}")
                        attack_stop_event.clear()
                        attack_task = asyncio.create_task(attack_loop(writer, attack_speed, attack_stop_event))
                # 2. New room with enemies
                if has_enemies(filtered):
                    if attack_task is None or attack_task.done():
                        print(f"{Fore.MAGENTA}[Enemies detected in room! Auto-attacking...]{Style.RESET_ALL}")
                        attack_stop_event.clear()
                        attack_task = asyncio.create_task(attack_loop(writer, attack_speed, attack_stop_event))
                # 3. Monster has died!
                if "Monster has died!" in filtered or "has died!" in filtered:
                    if attack_task and not attack_task.done():
                        print(f"{Fore.MAGENTA}[Enemy defeated! Stopping auto-attack.]{Style.RESET_ALL}")
                        attack_stop_event.set()
                        await attack_task
                        attack_task = None

                # Room detection, money pickup, etc. as before
                room_detected = detect_new_room(filtered)
                if room_detected and room_detected != last_room:
                    print(f"\n{Fore.CYAN}[You have entered: {room_detected}]{Style.RESET_ALL}\n")
                    last_room = room_detected
                    if acc_index is not None:
                        update_account_location(config, username, room_detected)

                amount = find_money_in_room(filtered)
                if amount and not aaf_dont_take_money:
                    print(f"\n{Fore.YELLOW}[Auto-picking up ${amount} found in the room!]{Style.RESET_ALL}")
                    print(color_send(f"take ${amount}"))
                    writer.write(f"take ${amount}\r\n")

                drop_amount = find_money_drop(filtered)
                if drop_amount and not aaf_dont_take_money:
                    print(f"\n{Fore.YELLOW}[Auto-picking up ${drop_amount} dropped!]{Style.RESET_ALL}")
                    print(color_send(f"take ${drop_amount}"))
                    writer.write(f"take ${drop_amount}\r\n")

                ground_drop_amount = find_money_ground_drop(filtered)
                if ground_drop_amount and not aaf_dont_take_money:
                    print(
                        f"\n{Fore.YELLOW}[Auto-picking up ${ground_drop_amount} dropped on the ground!]{Style.RESET_ALL}")
                    print(color_send(f"take ${ground_drop_amount}"))
                    writer.write(f"take ${ground_drop_amount}\r\n")

        except asyncio.TimeoutError:
            continue

    if attack_task and not attack_task.done():
        attack_stop_event.set()
        await attack_task
    writer.close()
    print("Connection closed")

if __name__ == '__main__':
    asyncio.run(main())
