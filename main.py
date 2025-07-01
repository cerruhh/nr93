import asyncio
import telnetlib3
import json
import re
import sys
from typing import Optional, Tuple, Dict, Any
from colorama import init, Fore, Style

init(autoreset=True)

CONFIG_FILE: str = "config.json"


def color_send(msg: str) -> str:
    return f"{Fore.RED}[nr]{Style.RESET_ALL} {Fore.WHITE}{msg}{Style.RESET_ALL}"


def parse_aliases(alias_list: list[str]) -> Dict[str, str]:
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


def update_account_location(config: Dict[str, Any], username: str, location: str) -> None:
    for acc in config['info'].get('accounts', []):
        if acc['username'] == username:
            acc['last_seen_location'] = normalize_location(location)
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            break


async def read_stdin(queue: asyncio.Queue[str]) -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, input, "> ")
        await queue.put(line)


async def choose_account(config: Dict[str, Any]) -> Tuple[str, str, Optional[int]]:
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
                    acc = accounts[int(idx) - 1]
                    return acc['username'], acc['password'], int(idx) - 1
                print("Invalid selection.")
    return info['username'], info['password'], None


async def attack_loop(writer: telnetlib3.TelnetWriter, attack_speed: float, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        writer.write("attack\r\n")
        await asyncio.sleep(attack_speed)


async def main() -> None:
    with open(CONFIG_FILE) as f:
        config: Dict[str, Any] = json.load(f)
    info = config['info']

    username, password, acc_index = await choose_account(config)
    aliases: Dict[str, str] = parse_aliases(info.get('aliases', []))
    aaf_dont_take_money: bool = info.get('aaf_dont_take_money', False)
    host: str = info['host']
    port: int = info['port']
    attack_speed: float = float(info.get('attack_speed', 5))

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
    last_room: Optional[str] = None

    enemy_count: int = 0
    attack_task: Optional[asyncio.Task] = None
    attack_stop_event: asyncio.Event = asyncio.Event()

    print(f"\n{Fore.GREEN}Connected to {host}:{port} as {username}.{Style.RESET_ALL} Type commands or !quit to exit.")

    while True:
        try:
            cmd: str = await asyncio.wait_for(input_queue.get(), timeout=0.1)
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
            data: Optional[str] = await asyncio.wait_for(reader.read(1024), timeout=0.1)
            if not data:
                break

            filtered: str = re.sub(r'\{.*?\}', '', data, flags=re.DOTALL)

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
                room_detected: Optional[str] = detect_new_room(filtered)
                if room_detected and room_detected != last_room:
                    print(f"\n{Fore.CYAN}[Entered: {room_detected}]{Style.RESET_ALL}")
                    last_room = room_detected
                    if acc_index is not None:
                        update_account_location(config, username, room_detected)

                    enemy_count = count_enemies(filtered)
                    if enemy_count > 0:
                        print(f"{Fore.MAGENTA}[Detected {enemy_count} enemies!]{Style.RESET_ALL}")

                if "Monster enters the room!" in filtered:
                    enemy_count += 1
                    print(f"{Fore.MAGENTA}[New enemy detected! Total: {enemy_count}]{Style.RESET_ALL}")

                death_matches = re.findall(r'(\w+ has died!|Monster has died!)', filtered, re.IGNORECASE)
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


if __name__ == '__main__':
    asyncio.run(main())
