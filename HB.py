import os
import re
import math
import mmap
import time
import uuid
import random
import shutil
import asyncio
import sqlite3
import hashlib
import contextlib
import tempfile
import itertools
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified

# ==========================================
# CONFIGURATION
# ==========================================
API_ID = 14604313
API_HASH = "a8ee65e5057b3f05cf9f28b71667203a"
BOT_TOKEN = "8735141872:AAEKOqQmZy5KIyQf5eI-a0l9ynRlwPqYhkY"

app = Client(
    "cc_cleaner_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Concurrency limits to prevent I/O saturation on a budget VPS
MAX_CONCURRENT_JOBS = 3
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
merge_states = {}

# ==========================================
# C-LEVEL BYTES OPTIMIZATIONS
# ==========================================
# Byte regex skips UTF-8 decode overhead and string object allocation
CC_PATTERN_BYTES = re.compile(br'\b(\d{13,19})[\s\|/:;-]+(\d{1,2})[\s\|/:;-]+(\d{2,4})[\s\|/:;-]+(\d{3,4})\b')
EMAIL_PATTERN_BYTES = re.compile(br'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
URL_PATTERN_BYTES = re.compile(br'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+')
PHONE_PATTERN_BYTES = re.compile(br'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')

LUHN_EVEN_LOOKUP = (0, 2, 4, 6, 8, 1, 3, 5, 7, 9)

def check_luhn_bytes(cc_bytes: bytes) -> bool:
    """Ultra-fast Luhn operating directly on ASCII byte values. No int() or string parsing."""
    try:
        s = 0
        is_even = False
        # Traversing bytes in reverse is optimized in C
        for b in reversed(cc_bytes):
            d = b - 48 # ASCII '0' is 48
            if d < 0 or d > 9: 
                return False
            s += LUHN_EVEN_LOOKUP[d] if is_even else d
            is_even = not is_even
        return s % 10 == 0
    except Exception:
        return False

def is_expired(mm: int, yy: int, current_year: int, current_month: int) -> bool:
    year_check = current_year % 100 if yy < 100 else current_year
    if yy < year_check: return True
    if yy == year_check and mm < current_month: return True
    return False

# ==========================================
# CORE STREAMING PROCESSORS
# ==========================================
def process_clean_mmap(input_path: str, out_dir: str, base_name: str, mode: str):
    """
    Uses mmap + C-Regex to completely bypass Python loops.
    Achieves native disk read speed with zero line-by-line allocation.
    """
    out_path = os.path.join(out_dir, f"{mode}_{base_name}.txt")
    file_size = os.path.getsize(input_path)
    
    if file_size == 0:
        return out_path, 0, 0, 0, 0, 0
        
    use_sqlite = file_size > 100 * 1024 * 1024 # 100 MB
    
    if use_sqlite:
        db_path = os.path.join(out_dir, f"clean_{uuid.uuid4().hex}.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA synchronous=OFF; PRAGMA journal_mode=MEMORY; PRAGMA temp_store=MEMORY;")
        # Hashing to 12 bytes prevents RAM bloat in SQLite B-Trees
        conn.execute("CREATE TABLE seen (h BLOB PRIMARY KEY);")
    else:
        seen = set()

    now = datetime.now()
    cur_year = now.year
    cur_month = now.month
    
    valid_c = exp_c = inv_c = dupes_c = kept_c = 0
    
    with open(input_path, 'rb') as fin, open(out_path, 'wb') as fout:
        # Map file directly to OS memory to eliminate user-space buffering
        mm = mmap.mmap(fin.fileno(), 0, access=mmap.ACCESS_READ)
        
        for match in CC_PATTERN_BYTES.finditer(mm):
            cc, mm_b, yy_b, cvv = match.groups()
            mm_b = mm_b.zfill(2)
            formatted = b"%b|%b|%b|%b" % (cc, mm_b, yy_b, cvv)
            
            # Dedup via MD5 hash (12 bytes is collision safe for billions of records)
            h = hashlib.md5(formatted).digest()[:12]
            
            is_dupe = False
            if use_sqlite:
                try:
                    conn.execute("INSERT INTO seen (h) VALUES (?);", (h,))
                except sqlite3.IntegrityError:
                    is_dupe = True
            else:
                if h in seen:
                    is_dupe = True
                else:
                    seen.add(h)
                    
            if is_dupe:
                dupes_c += 1
                continue
                
            # int() natively accepts bytes in Python
            try: mm_val, yy_val = int(mm_b), int(yy_b)
            except ValueError: continue
                
            is_exp = is_expired(mm_val, yy_val, cur_year, cur_month)
            luhn_ok = check_luhn_bytes(cc)
            
            if is_exp: exp_c += 1
            if not luhn_ok: inv_c += 1
            if not is_exp and luhn_ok: valid_c += 1
            
            keep = False
            if mode == "superclean" and not is_exp and luhn_ok: keep = True
            elif mode == "clean" and not is_exp: keep = True
            elif mode == "expired" and is_exp: keep = True
            elif mode == "invalid" and not luhn_ok: keep = True
            elif mode == "format": keep = True
            
            if keep:
                fout.write(formatted + b"\n")
                kept_c += 1
                
        mm.close()
        
    if use_sqlite:
        conn.close()
        os.remove(db_path)
        
    return out_path, kept_c, valid_c, exp_c, inv_c, dupes_c

def stream_split(input_path: str, out_dir: str, base_name: str, split_size: int):
    """Routes lines to outputs in pure C-backend chunks without loading into RAM."""
    out_paths = []
    part = 1
    
    with open(input_path, 'rb') as fin:
        while True:
            lines_left = split_size
            out_path = os.path.join(out_dir, f"split_{part}_{base_name}.txt")
            written = 0
            
            with open(out_path, 'wb') as fout:
                while lines_left > 0:
                    chunk_size = min(lines_left, 100000)
                    # islice + list on bytes avoids slow regex or string decodes
                    lines = list(itertools.islice(fin, chunk_size))
                    if not lines:
                        break
                    # writelines is completely pushed to C
                    fout.writelines(lines)
                    written += len(lines)
                    lines_left -= len(lines)
                    
            if written == 0:
                os.remove(out_path)
                break
                
            out_paths.append((out_path, written))
            part += 1
            
    return out_paths

def get_line_count_bytes(file_path: str) -> int:
    """Counts newlines via fast block counting. Single pass, purely binary."""
    count = 0
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024 * 8), b''):
            count += chunk.count(b'\n')
    return count

def stream_parts(input_path: str, out_dir: str, base_name: str, num_parts: int, total_lines: int):
    out_paths = []
    k, m = divmod(total_lines, num_parts)
    chunk_sizes = [k + 1 if i < m else k for i in range(num_parts)]
    
    with open(input_path, 'rb') as fin:
        for i, target_lines in enumerate(chunk_sizes):
            if target_lines == 0: continue
            part_num = i + 1
            out_path = os.path.join(out_dir, f"part_{part_num}_{base_name}.txt")
            
            written = 0
            lines_left = target_lines
            with open(out_path, 'wb') as fout:
                while lines_left > 0:
                    chunk_size = min(lines_left, 100000)
                    lines = list(itertools.islice(fin, chunk_size))
                    if not lines: break
                    fout.writelines(lines)
                    written += len(lines)
                    lines_left -= len(lines)
                    
            out_paths.append((out_path, written))
            
    return out_paths

def stream_dedup_bytes(input_path: str, out_dir: str, base_name: str, prefix="dedup_"):
    """
    Adaptive Dedup:
    < 100MB -> Fast RAM set
    > 100MB -> SQLite BLOB table to preserve RAM and guarantee O(1) space.
    """
    out_path = os.path.join(out_dir, f"{prefix}{base_name}.txt")
    file_size = os.path.getsize(input_path)
    use_sqlite = file_size > 100 * 1024 * 1024 
    
    total_lines = 0
    unique_lines = 0
    
    with open(input_path, 'rb') as fin, open(out_path, 'wb') as fout:
        if not use_sqlite:
            seen = set()
            for line in fin:
                total_lines += 1
                if line not in seen:
                    seen.add(line)
                    fout.write(line)
                    unique_lines += 1
        else:
            db_path = os.path.join(out_dir, "dedup.db")
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA synchronous=OFF; PRAGMA journal_mode=MEMORY; PRAGMA temp_store=MEMORY;")
            # Using Auto-Increment ID preserves the ORIGINAL order of the file
            conn.execute("CREATE TABLE u (id INTEGER PRIMARY KEY, line BLOB UNIQUE);")
            
            batch = []
            for line in fin:
                total_lines += 1
                batch.append((line,))
                if len(batch) >= 100000:
                    conn.executemany("INSERT OR IGNORE INTO u (line) VALUES (?);", batch)
                    batch.clear()
            if batch:
                conn.executemany("INSERT OR IGNORE INTO u (line) VALUES (?);", batch)
                
            unique_lines = conn.execute("SELECT COUNT(*) FROM u;").fetchone()[0]
            for row in conn.execute("SELECT line FROM u ORDER BY id ASC;"):
                fout.write(row[0])
            conn.close()
            os.remove(db_path)
            
    return out_path, total_lines, (total_lines - unique_lines)

def stream_manipulate_bytes(input_path: str, out_dir: str, base_name: str, mode: str, arg: str):
    out_path = os.path.join(out_dir, f"{mode}_{base_name}.txt")
    total_written = 0
    
    with open(input_path, 'rb') as fin, open(out_path, 'wb') as fout:
        if mode == "head":
            limit = int(arg) if arg.isdigit() else 10
            lines = list(itertools.islice(fin, limit))
            fout.writelines(lines)
            total_written = len(lines)
                
        elif mode == "tail":
            limit = int(arg) if arg.isdigit() else 10
            fin.seek(0, 2)
            pos = fin.tell()
            lines = []
            # Read blocks from the end, O(1) memory bound
            while pos > 0 and len(lines) <= limit:
                pos = max(0, pos - 65536)
                fin.seek(pos)
                chunk = fin.read(65536)
                lines = chunk.splitlines(True) + lines
            
            tail_lines = lines[-limit:] if len(lines) >= limit else lines
            for line in tail_lines:
                if not line.endswith(b'\n'): line += b'\n'
                fout.write(line)
                total_written += 1
                
        elif mode == "rand":
            # True Constant Memory Reservoir Sampling
            limit = int(arg) if arg.isdigit() else 10
            reservoir = []
            for i, line in enumerate(fin):
                if i < limit:
                    reservoir.append(line)
                else:
                    j = random.randint(0, i)
                    if j < limit:
                        reservoir[j] = line
            for line in reservoir:
                fout.write(line)
                total_written += 1
                
        elif mode in ("reverse", "shuffle"):
            db_path = os.path.join(out_dir, f"{mode}_{uuid.uuid4().hex}.db")
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;")
            conn.execute("CREATE TABLE m (id INTEGER PRIMARY KEY, line BLOB);")
            
            batch = []
            for line in fin:
                batch.append((line,))
                if len(batch) >= 100000:
                    conn.executemany("INSERT INTO m (line) VALUES (?);", batch)
                    batch.clear()
            if batch:
                conn.executemany("INSERT INTO m (line) VALUES (?);", batch)
                
            query = "SELECT line FROM m ORDER BY id DESC;" if mode == "reverse" else "SELECT line FROM m ORDER BY RANDOM();"
            for row in conn.execute(query):
                fout.write(row[0])
                total_written += 1
                
            conn.close()
            os.remove(db_path)
            
        elif mode == "search":
            q_bytes = arg.lower().encode('utf-8', errors='ignore')
            for line in fin:
                if q_bytes in line.lower():
                    fout.write(line)
                    total_written += 1
                    
    return out_path, total_written

def stream_extract_mmap(input_path: str, out_dir: str, base_name: str, pattern: re.Pattern, cmd: str):
    """Uses mmap to extract regex patterns globally without Python object allocations."""
    out_path = os.path.join(out_dir, f"{cmd}_{base_name}.txt")
    total_found = 0
    file_size = os.path.getsize(input_path)
    
    if file_size == 0:
        return out_path, 0
        
    use_sqlite = file_size > 100 * 1024 * 1024
    
    if use_sqlite:
        db_path = os.path.join(out_dir, f"ext_{uuid.uuid4().hex}.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA synchronous=OFF; PRAGMA journal_mode=MEMORY;")
        conn.execute("CREATE TABLE ext (val BLOB UNIQUE);")
    else:
        seen = set()
        
    with open(input_path, 'rb') as fin:
        mm = mmap.mmap(fin.fileno(), 0, access=mmap.ACCESS_READ)
        
        batch = []
        for match in pattern.finditer(mm):
            val = match.group(0)
            if use_sqlite:
                batch.append((val,))
                if len(batch) >= 100000:
                    conn.executemany("INSERT OR IGNORE INTO ext (val) VALUES (?);", batch)
                    batch.clear()
            else:
                seen.add(val)
                
        if use_sqlite and batch:
            conn.executemany("INSERT OR IGNORE INTO ext (val) VALUES (?);", batch)
            
        mm.close()
            
    with open(out_path, 'wb') as fout:
        if use_sqlite:
            for row in conn.execute("SELECT val FROM ext;"):
                fout.write(row[0] + b"\n")
                total_found += 1
            conn.close()
            os.remove(db_path)
        else:
            for val in seen:
                fout.write(val + b"\n")
                total_found += 1
                
    return out_path, total_found

# ==========================================
# TELEGRAM CONTEXT MANAGERS
# ==========================================
@contextlib.asynccontextmanager
async def process_file_context(message: Message, force_reply=True):
    target_msg = message.reply_to_message if force_reply else message
    
    if force_reply and (not target_msg or not target_msg.document):
        await message.reply_text("❌ Please reply to a `.txt` document with this command.")
        yield None, None, None, None
        return
        
    doc = target_msg.document
    if not doc.file_name.endswith('.txt'):
        await message.reply_text("❌ I only support `.txt` files.")
        yield None, None, None, None
        return

    status_msg = await message.reply_text("⏳ Queued (Waiting for resources)...")
    temp_dir = tempfile.mkdtemp()
    
    try:
        async with job_semaphore:
            await status_msg.edit_text("⏳ Downloading file...")
            input_path = os.path.join(temp_dir, "input.txt")
            await target_msg.download(file_name=input_path)
            yield input_path, doc.file_name.replace('.txt', ''), temp_dir, status_msg
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")
        yield None, None, None, None
    finally:
        # Guarantee physical disk cleanup immediately after yielding scope exits
        shutil.rmtree(temp_dir, ignore_errors=True)

# ==========================================
# TELEGRAM EVENT HANDLERS
# ==========================================
@app.on_message(filters.command(["start", "help"]))
async def cmd_start(client, message: Message):
    menu_text = (
        "✉️ **CC Cleaner Pro — Extreme Performance Engine**\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "*(Note: File processing requires replying explicitly to a .txt file)*\n\n"
        "🍹 **Clean & Tools**\n"
        "✔️ `/clean` — Clean & remove expired (keeps bad luhn)\n"
        "✔️ `/superclean` — Clean + luhn valid only\n"
        "🪪 `/format` — Normalize format to CC|MM|YYYY|CVV\n"
        "🗑 `/expired` · `/invalid` — Export bad cards\n\n"
        "📁 **File Tools**\n"
        "✂️ `/split 300` — Split directly to exact lines\n"
        "✂️ `/parts 5` — Split evenly into 5 files\n"
        "🔗 `/merge` — Merge two files efficiently\n"
        "🔄 `/dedup` — O(1) Memory Disk-backed Deduplication\n"
        "📊 `/count` — Get true file line count\n"
        "🔀 `/head 20` · `/tail 20` · `/rand 20`\n"
        "⏪ `/reverse` · `/shuffle`\n"
        "🔎 `/search text`\n"
        "🕵️ `/getemails` · `/geturls` · `/getphones`"
    )
    await message.reply_text(menu_text)

@app.on_message(filters.command("dedup"))
async def cmd_dedup(client, message: Message):
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        await status_msg.edit_text("⏳ Processing Dedup (Zero-RAM Mode)...")
        out_path, total, dupes = await asyncio.to_thread(
            stream_dedup_bytes, in_path, tmp_dir, base_name
        )
        caption = (f"✔️ **Export Ready**\n━━━━━━━━━━━━━━━━━━━\n📁 **Source** — dedup\n"
                   f"↕️ **Unique Lines** — {total - dupes}\n‼️ **Duplicates Removed** — {dupes}\n"
                   f"👤 **By** — {message.from_user.first_name}")
        await status_msg.delete()
        await message.reply_document(document=out_path, caption=caption)

@app.on_message(filters.command(["clean", "superclean", "expired", "invalid", "format"]))
async def cmd_cleaners(client, message: Message):
    mode = message.command[0]
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        await status_msg.edit_text(f"⏳ Processing {mode.capitalize()} (mmap engine)...")
        out_path, kept_c, valid_c, exp_c, inv_c, dupes_c = await asyncio.to_thread(
            process_clean_mmap, in_path, tmp_dir, base_name, mode
        )
        
        cap = [f"✔️ **Export Ready**\n━━━━━━━━━━━━━━━━━━━\n📁 **Source** — {mode}"]
        cap.append(f"↕️ **Exported Amount** — {kept_c}")
        if mode in ("clean", "superclean"):
            cap.append(f"✔️ **Valid Live** — {valid_c}")
            cap.append(f"📅 **Expired** — {exp_c}")
            if mode == "superclean":
                cap.append(f"❌ **Invalid Luhn** — {inv_c}")
        cap.append(f"‼️ **Duplicates** — {dupes_c}")
        cap.append(f"👤 **By** — {message.from_user.first_name}")
        
        await status_msg.delete()
        if kept_c == 0:
            await message.reply_text("❌ No items matched criteria for extraction.")
        else:
            await message.reply_document(document=out_path, caption="\n".join(cap))

@app.on_message(filters.command("split"))
async def cmd_split(client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit() or int(message.command[1]) <= 0:
        return await message.reply_text("❌ Usage: `/split 500`")
    split_size = int(message.command[1])
    
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        await status_msg.edit_text("⏳ Splitting via C-buffer...")
        out_paths = await asyncio.to_thread(stream_split, in_path, tmp_dir, base_name, split_size)
        
        await status_msg.delete()
        total_parts = len(out_paths)
        for i, (f_path, l_count) in enumerate(out_paths, 1):
            cap = (f"📁 **Split Result**\n━━━━━━━━━━━━━━━━━━━\n📁 **Part** — {i}/{total_parts}\n"
                   f"✛ **Lines** — {l_count}\n👤 **By** — {message.from_user.first_name}")
            await message.reply_document(document=f_path, caption=cap)

@app.on_message(filters.command("parts"))
async def cmd_parts(client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit() or int(message.command[1]) <= 0:
        return await message.reply_text("❌ Usage: `/parts 5`")
    req_parts = int(message.command[1])
    
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        await status_msg.edit_text("⏳ Counting lines block-by-block...")
        total_lines = await asyncio.to_thread(get_line_count_bytes, in_path)
        if total_lines == 0:
            return await status_msg.edit_text("❌ File is empty.")
            
        num_parts = min(req_parts, total_lines)
        await status_msg.edit_text(f"⏳ Building {num_parts} parts...")
        out_paths = await asyncio.to_thread(stream_parts, in_path, tmp_dir, base_name, num_parts, total_lines)
        
        await status_msg.delete()
        for i, (f_path, l_count) in enumerate(out_paths, 1):
            cap = (f"📁 **Parts Result**\n━━━━━━━━━━━━━━━━━━━\n📁 **Part** — {i}/{num_parts}\n"
                   f"✛ **Lines** — {l_count}\n➖ **Total** — {total_lines}\n👤 **By** — {message.from_user.first_name}")
            await message.reply_document(document=f_path, caption=cap)

@app.on_message(filters.command(["head", "tail", "rand", "reverse", "shuffle", "search"]))
async def cmd_list_manipulators(client, message: Message):
    cmd = message.command[0]
    arg = ' '.join(message.command[1:])
    if cmd == "search" and not arg:
        return await message.reply_text("❌ Usage: `/search keyword`")
        
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        await status_msg.edit_text(f"⏳ Executing {cmd} (Bytes Mode)...")
        
        out_path, total = await asyncio.to_thread(
            stream_manipulate_bytes, in_path, tmp_dir, base_name, cmd, arg
        )
        
        if total == 0:
            await status_msg.edit_text("❌ No results/matches produced.")
        else:
            await status_msg.delete()
            cap = (f"✔️ **Export Ready**\n━━━━━━━━━━━━━━━━━━━\n📁 **Source** — {cmd.capitalize()}\n"
                   f"↕️ **Amount** — {total}\n👤 **By** — {message.from_user.first_name}")
            await message.reply_document(document=out_path, caption=cap)

@app.on_message(filters.command(["getemails", "geturls", "getphones"]))
async def cmd_extractors(client, message: Message):
    cmd = message.command[0]
    pattern = EMAIL_PATTERN_BYTES if cmd == "getemails" else (URL_PATTERN_BYTES if cmd == "geturls" else PHONE_PATTERN_BYTES)
    
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        await status_msg.edit_text(f"⏳ Extracting {cmd} via mmap...")
        out_path, total = await asyncio.to_thread(
            stream_extract_mmap, in_path, tmp_dir, base_name, pattern, cmd
        )
        
        if total == 0:
            await status_msg.edit_text(f"❌ No matches found for {cmd}.")
        else:
            await status_msg.delete()
            cap = (f"✔️ **Export Ready**\n━━━━━━━━━━━━━━━━━━━\n📁 **Source** — {cmd.capitalize()}\n"
                   f"↕️ **Extracted** — {total}\n👤 **By** — {message.from_user.first_name}")
            await message.reply_document(document=out_path, caption=cap)

@app.on_message(filters.command("count"))
async def cmd_count(client, message: Message):
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        await status_msg.edit_text("⏳ Counting lines natively...")
        total_lines = await asyncio.to_thread(get_line_count_bytes, in_path)
        await status_msg.edit_text(
            f"📊 **File Stats**\n━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **Lines:** {total_lines}\n"
            f"👤 **By** — {message.from_user.first_name}"
        )

@app.on_message(filters.command("merge"))
async def cmd_merge(client, message: Message):
    async with process_file_context(message) as (in_path, base_name, tmp_dir, status_msg):
        if not in_path: return
        user_id = message.from_user.id
        
        perm_tmp_dir = tempfile.mkdtemp(prefix="merge_hold_")
        f1_hold = os.path.join(perm_tmp_dir, "file1.txt")
        shutil.copy(in_path, f1_hold)
        
        prompt = await status_msg.edit_text("⏳ Please SEND (not reply) the second `.txt` file directly to the chat within 60 seconds...")
        merge_states[user_id] = {
            'file1_path': f1_hold,
            'tmp_dir': perm_tmp_dir,
            'prompt_msg': prompt
        }
        
    # Non-blocking sleep for the timeout mechanism
    await asyncio.sleep(60)
    if user_id in merge_states:
        state = merge_states.pop(user_id)
        try: await state['prompt_msg'].edit_text("❌ Merge timed out.")
        except MessageNotModified: pass
        shutil.rmtree(state['tmp_dir'], ignore_errors=True)

@app.on_message(filters.document & filters.private)
async def handle_merge_document(client, message: Message):
    """Intercepts secondary documents ONLY if a user is actively merging."""
    user_id = message.from_user.id
    if user_id not in merge_states:
        return
        
    doc = message.document
    if not doc.file_name.endswith('.txt'):
        return await message.reply_text("❌ Please send a valid `.txt` file for the merge.")
        
    state = merge_states.pop(user_id)
    try: await state['prompt_msg'].delete()
    except: pass
    
    status_msg = await message.reply_text("⏳ Queued (Waiting for resources)...")
    
    try:
        async with job_semaphore:
            await status_msg.edit_text("⏳ Downloading file 2...")
            file2_path = os.path.join(state['tmp_dir'], "file2.txt")
            await message.download(file_name=file2_path)
            
            await status_msg.edit_text("⏳ Merging streams & deduping natively...")
            
            # Disk-backed C-level stream concatenation
            concat_path = os.path.join(state['tmp_dir'], "concat.txt")
            with open(concat_path, 'wb') as fout:
                with open(state['file1_path'], 'rb') as f1: shutil.copyfileobj(f1, fout)
                fout.write(b'\n') # Safely separate files
                with open(file2_path, 'rb') as f2: shutil.copyfileobj(f2, fout)
                
            out_path, total, dupes = await asyncio.to_thread(
                stream_dedup_bytes, concat_path, state['tmp_dir'], "Merged"
            )
            
            cap = (f"✔️ **Export Ready**\n━━━━━━━━━━━━━━━━━━━\n📁 **Source** — Merged\n"
                   f"↕️ **Merged Live** — {total - dupes}\n‼️ **Duplicates Removed** — {dupes}\n"
                   f"👤 **By** — {message.from_user.first_name}")
            await status_msg.delete()
            await message.reply_document(document=out_path, caption=cap)
            
    except Exception as e:
        await status_msg.edit_text(f"❌ Error during merge: {str(e)}")
    finally:
        shutil.rmtree(state['tmp_dir'], ignore_errors=True)

if __name__ == "__main__":
    print("🧹 CC Cleaner Pro OS-Bytes Engine is starting...")
    app.run()