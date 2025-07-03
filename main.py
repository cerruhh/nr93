import asyncio
import telnetlib3
import json
import bigjson
import re
from colorama import init, Fore, Style
from typing import List, Dict, Optional, Tuple, Any
from collections import deque

init(autoreset=True)

CONFIG_FILE: str = "config.json"
MAP_EXPORT: str = "storage/mapExport.json"

def color_send(msg: str, is_written:bool = True) -> str:
    if is_written:
        return f"{Fore.RED}[nr]{Style.RESET_ALL} {Fore.WHITE}{msg}{Style.RESET_ALL}"
    else:
        return f"{Fore.RED}[nr local]{Style.RESET_ALL} {Fore.WHITE}{msg}{Style.RESET_ALL}"

# ======= ROOMS START ========== #

def load_rooms(map_export_file: str) -> List[Dict]:
    with open(map_export_file, "rb") as f:
        data = bigjson.load(f)
    return data[0]["rooms"]

def find_path(rooms: List[Dict], start_id: int, end_id: int) -> Optional[List[str]]:
    """
    Find the shortest path from start_id to end_id using only 'exits'.
    Returns a list of directions (e.g. ['west', 'south', 'east']) or None if no path found.
    """
    # Build a lookup: id -> room dict
    # rooms = load_rooms(map_export_file=MAP_EXPORT)
    id_to_room = {room["id"]: room for room in rooms}
    if start_id not in id_to_room or end_id not in id_to_room:
        print(len(id_to_room))
        print(id_to_room)
        return None

    queue = deque()
    queue.append((start_id, []))
    visited = set()

    while queue:
        current_id, path = queue.popleft()
        if current_id == end_id:
            return path
        if current_id in visited:
            continue
        visited.add(current_id)
        exits = id_to_room[current_id].get("exits", {})
        for direction, neighbor_id in exits.items():
            # Defensive: skip if neighbor_id not in map (broken exit)
            if neighbor_id not in id_to_room:
                continue
            if neighbor_id not in visited:
                queue.append((neighbor_id, path + [direction]))
    return None

def path_to_commands(path: List[str]) -> List[str]:
    """Compact consecutive directions: ['west', 'west', 'south', 'east', 'east', 'east'] -> ['west 2', 'south 1', 'east 3']"""
    if not path:
        return []
    commands = []
    last_dir = path[0]
    count = 1
    for d in path[1:]:
        if d == last_dir:
            count += 1
        else:
            commands.append(f"{last_dir} {count}")
            last_dir = d
            count = 1
    commands.append(f"{last_dir} {count}")
    return commands
# ======= ROOMS END ========== #

def parse_aliases(alias_list: list[str]) -> Dict[str, str]:
    """
    Parses the aliases in the config.json file
    """
    aliases: Dict[str, str] = {}
    for entry in alias_list:
        if "|" in entry:
            key, value = entry.split("|", 1)
            aliases[key.strip()] = value.strip()
    return aliases


def detect_new_room(text: str) -> Optional[str]:
    lines = text.strip().splitlines()
    if len(lines) < 3:
        return None
    room_name = lines[0].strip()
    if re.match(r'^[A-Z][A-Za-z0-9 \-]+$', room_name) and any("exit" in l.lower() for l in lines[2:4]):
        return room_name
    return None


def find_money_in_room(text: str) -> Optional[str]:
    match = re.search(r'You see: \$([0-9]+)', text)
    return match.group(1) if match else None


def find_money_drop(text: str) -> Optional[str]:
    match = re.search(r'drops \$([0-9]+)\.', text)
    return match.group(1) if match else None


def find_money_ground_drop(text: str) -> Optional[str]:
    match = re.search(r'\$([0-9]+) drops to the ground\.', text)
    return match.group(1) if match else None


def has_enemies(text: str) -> bool:
    m = re.search(r'Enemies:\s*(.+)', text)
    if m:
        enemies = m.group(1).strip()
        return bool(enemies) and enemies.lower() != "none"
    return False


def count_enemies(text: str) -> int:
    m = re.search(r'Enemies:\s*(.+)', text)
    if m:
        enemies_str = m.group(1).strip()
        if enemies_str.lower() == "none":
            return 0
        enemies = [e.strip() for e in enemies_str.split(',') if e.strip()]
        return len(enemies)
    return 0


def normalize_location(name: str) -> str:
    return name.lower().replace(" ", "")


# def update_account_location(config: Dict[str, Any], username: str, location: str) -> None:
#     for acc in config['info'].get('accounts', []):
#         if acc['username'] == username:
#             acc['last_seen_location'] = normalize_location(location)
#             with open(CONFIG_FILE, "w") as f:
#                 json.dump(config, f, indent=2)
#             break

async def read_stdin(queue: asyncio.Queue[str]) -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, input, "> ")
        await queue.put(line)


async def choose_account(config: Dict[str, Any]) -> Tuple[str, str, Optional[int]]:
    """
    Log the user in.
    The choice can be a number and it will be accepted as an account index.
    """
    info = config['info']
    accounts = info.get('accounts', [])
    print(f"Host: {info['host']}  Port: {info['port']}")
    print(f"Main account: {info['username']}")
    if accounts:
        print("Alternate accounts:")
        for idx, acc in enumerate(accounts, 1):
            loc = acc.get("last_seen_location", "unknown")
            print(f"  {idx}: {acc['username']} (last seen: {loc})")
        if info.get("auto-log-user", {'enabled': False}).get("enabled"):
            idx = info.get("auto-log-user").get("user-index") - 1
            acc = accounts[idx]
            return acc['username'], acc['password'], idx

        choice = input("Use alternate account? (y/n): ").strip().lower()
        if choice == "y":
            while True:
                idx = input(f"Select account [1-{len(accounts)}]: ").strip()
                if idx.isdigit() and 1 <= int(idx) <= len(accounts):
                    acc = accounts[int(idx) - 1]
                    return acc['username'], acc['password'], int(idx) - 1
                print("Invalid selection.")
        elif choice.isdigit() and 1 <= int(choice) <= len(accounts):
            # You can here just enter an account index instead of pressing y first. It's kinda hidden because I like it.
            acc = accounts[int(choice) - 1]
            return acc['username'], acc['password'], int(choice) - 1

    # Just the default user
    return info['username'], info['password'], None


async def attack_loop(writer: telnetlib3.TelnetWriter, attack_speed: float, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        writer.write("attack\r\n")
        color_send(msg=f"(atk)")
        await asyncio.sleep(attack_speed)

async def loopatk_loop(writer: telnetlib3.TelnetWriter, attack_speed: float, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        writer.write("a\r\n")
        print("[atk]")
        await asyncio.sleep(attack_speed)


def concat_color(items: list[str]) -> str:
    # Remove style keywords from message
    return ' '.join([item for item in items[2:] if item.lower() not in {"blink", "bold", "italic"}])

async def main() -> None:
    with open(CONFIG_FILE) as f:
        config: Dict[str, Any] = json.load(f)

    loopatk_task: Optional[asyncio.Task] = None
    loopatk_stop_event: asyncio.Event = asyncio.Event()
    info = config['info']

    # Room nav
    # start_id = 1
    # end_id = 160

    username, password, acc_index = await choose_account(config)
    aliases: Dict[str, str] = parse_aliases(info.get('aliases', []))
    aliases_list:List[str] = info["aliases"]
    aaf_dont_take_money: bool = info.get('aaf_dont_take_money', False)
    host: str = info['host']
    port: int = info['port']
    attack_speed: float = float(info.get('attack_speed', 5))

    auto_log_user_info:dict = info.get("auto-log-user", {'enabled': False})

    auto_loop_atk_val = auto_log_user_info.get("instant-autoloopatk", False)


    whisper_party:Optional[str] = ""

    reader, writer = await telnetlib3.open_connection(host, port)
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    asyncio.create_task(read_stdin(input_queue))

    # Login sequence
    await asyncio.sleep(0.5)
    writer.write(username + "\r\n")
    await asyncio.sleep(0.5)
    writer.write(password + "\r\n")

    last_command: str = ""
    json_mode: bool = False
    json_buffer: str = ""

    attack_task: Optional[asyncio.Task] = None
    attack_stop_event: asyncio.Event = asyncio.Event()

    print(f"\n{Fore.GREEN}Connected to {host}:{port} as {username}.{Style.RESET_ALL} Type commands or !quit to exit.")


    # log autoatk
    if auto_loop_atk_val:
        await asyncio.sleep(0.5)
        if loopatk_task and not loopatk_task.done():
            print(color_send("Loopatk is already running!", is_written=False))
        else:
            print(color_send("Starting loopatk: sending 'a' every {:.2f} seconds.".format(attack_speed),
                             is_written=False))
            loopatk_stop_event = asyncio.Event()
            loopatk_task = asyncio.create_task(loopatk_loop(writer, attack_speed, loopatk_stop_event))


    while True:
        try:
            write_chat = True
            cmd: str = await asyncio.wait_for(input_queue.get(), timeout=0.1)
            if cmd == "!quit":
                break

            if cmd == "!gotohell":
                writer.write("fuck" + "\r\n")
                write_chat = False

            if loopatk_task and not loopatk_task.done() and cmd.strip() != "!loopatk":
                loopatk_stop_event.set()
                await loopatk_task
                loopatk_task = None

            if cmd.strip() == "!loopatk":
                if loopatk_task and not loopatk_task.done():
                    print(color_send("Loopatk is already running!", is_written=False))
                else:
                    print(color_send("Starting loopatk: sending 'a' every {:.2f} seconds.".format(attack_speed),
                                     is_written=False))
                    loopatk_stop_event = asyncio.Event()
                    loopatk_task = asyncio.create_task(loopatk_loop(writer, attack_speed, loopatk_stop_event))
                continue  # Don't send !loopatk to the server

            if not cmd:
                cmd = last_command
            elif cmd != "api":
                last_command = cmd
            if cmd.startswith('!') and cmd[1:] in aliases:
                cmd = aliases[cmd[1:]]
            elif cmd == "!":
                for alias in aliases_list:
                    split = alias.split(sep="|")
                    exe = split[1]
                    al = split[0]
                    print(f"{al} -> {exe}")
                    del split
                    del exe
                write_chat = False


            if cmd == "api":
                json_mode = True
                json_buffer = ""

            if cmd.startswith("!wh"):
                if whisper_party != "":
                    split = cmd.split(sep=" ")
                    if len(split) >= 2:
                        final_msg = ' '.join(split[1:])
                        cmd = f"whisper {whisper_party} {final_msg}"

            if cmd.startswith("!setwhisper"):
                write_chat = False
                old_whisper_party = whisper_party if whisper_party != "" else None
                split = cmd.split(sep=" ")
                if len(split) == 2:
                    whisper_party = split[1]
                    print(f"[Whisper party {old_whisper_party} -> {whisper_party}]")
                else:
                    print("[Invalid split lenght]")

            if cmd.startswith("!color"):
                split = cmd.split(sep=" ")
                if len(split) >= 3:
                    sel_color = split[1]
                    styles = {item.lower() for item in split[2:]}
                    ansi_styles = ""
                    if "blink" in styles:
                        ansi_styles += "\x1b[5m"
                    if "bold" in styles:
                        ansi_styles += "\x1b[1m"
                    if "italic" in styles:
                        ansi_styles += "\x1b[3m"
                    msg = concat_color(items=split)
                    cmd = f"{ansi_styles}\x1b[38;5;{sel_color}m{msg}"


            if cmd == "!aaf":
                aaf_dont_take_money = not aaf_dont_take_money
                print(f"[aaf set to {aaf_dont_take_money}]")
                write_chat = False

            print(color_send(cmd, is_written=write_chat))
            if write_chat:
                cmd = re.sub(r"fuck", "funk", cmd, flags=re.IGNORECASE)
                writer.write(cmd.replace("fuck", "funk") + "\r\n")
        except asyncio.TimeoutError:
            pass

        try:
            data: Optional[str] = await asyncio.wait_for(reader.read(1024), timeout=0.1)
            if not data:
                break

            filtered: str = re.sub(r'\{.*?}', '', data, flags=re.DOTALL)

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
                enemy_count = count_enemies(filtered)
                if enemy_count > 0:
                    print(f"{Fore.MAGENTA}[Detected {enemy_count} enemies!]{Style.RESET_ALL}")

                if "enters the room!" in filtered:
                    enemy_count += 1
                    print(f"{Fore.MAGENTA}[New enemy detected! Total: {enemy_count}]{Style.RESET_ALL}")

                death_matches = re.findall(r'(\w+ has died!)', filtered, re.IGNORECASE)
                if death_matches:
                    enemy_count = max(0, enemy_count - len(death_matches))
                    print(f"{Fore.MAGENTA}[Enemy defeated! Remaining: {enemy_count}]{Style.RESET_ALL}")

                if enemy_count > 0:
                    if attack_task is None or attack_task.done():
                        print(f"{Fore.MAGENTA}[Starting auto-attack against {enemy_count} enemies...]{Style.RESET_ALL}")
                        attack_stop_event.clear()
                        attack_task = asyncio.create_task(attack_loop(writer, attack_speed, attack_stop_event))
                else:
                    if attack_task and not attack_task.done():
                        print(f"{Fore.MAGENTA}[All enemies defeated! Stopping auto-attack.]{Style.RESET_ALL}")
                        attack_stop_event.set()

                for detector, msg in [
                    (find_money_in_room, "found in the room"),
                    (find_money_drop, "dropped"),
                    (find_money_ground_drop, "dropped on the ground")
                ]:
                    amount: Optional[str] = detector(filtered)
                    if amount and not aaf_dont_take_money:
                        print(f"\n{Fore.YELLOW}[Auto-picking up ${amount} {msg}!]{Style.RESET_ALL}")
                        print(color_send(f"take ${amount}"))
                        writer.write(f"take ${amount}\r\n")

                if filtered.strip():
                    print(filtered, end='')

        except asyncio.TimeoutError:
            continue

    if attack_task and not attack_task.done():
        attack_stop_event.set()
        await attack_task
    writer.close()
    print("Connection closed")

def cli():
    asyncio.run(main())

if __name__ == '__main__':
    cli()
