import asyncio
import aiohttp
import traceback
import os
import shutil
import zipfile
import tempfile
from datetime import datetime

# Pyroblack takes over the pyrogram namespace
from pyrogram import Client, filters, idle, enums
from pyrogram.types import Message
# ListenerTimeout is natively included in Pyroblack's error classes
from pyrogram.errors import BadRequest, FloodWait, ListenerTimeout

# MongoDB and GitHub
from motor.motor_asyncio import AsyncIOMotorClient
from github import Github
from github.GithubException import BadCredentialsException, GithubException

import logging

# ================= Configuration =================
API_ID = int(os.environ.get("Api", 14604313))
API_HASH = os.environ.get("Hash", "a8ee65e5057b3f05cf9f28b71667203a")
TOKEN = os.environ.get("token", "7067158275:AAFv4VPX3VlpP2lG6LxfxmibjMeYdI8k3uc")#"7067158275:AAEjp5Rwro9Slzlc6ZDHJDTL6lic5L2mol8")

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("Botlog.txt", mode="w"),
        logging.StreamHandler(),
    ],
    datefmt="%d/%b/%Y | %H:%M:%S %p",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Initialize Bot
app = Client("main_bot", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN)

class Translation:
    STATUS_TXT = """<b>᚛› 𝚃𝙾𝚃𝙰𝙻 𝙵𝙸𝙻𝙴𝚂: <code>{}</code></b>
<b>᚛› 𝚃𝙾𝚃𝙰𝙻 𝚄𝚂𝙴𝚁𝚂: <code>{}</code></b>
<b>᚛› 𝚃𝙾𝚃𝙰𝙻 𝙲𝙷𝙰𝚃𝚂: <code>{}</code></b>
<b>᚛› 𝚄𝚂𝙴𝙳 𝚂𝚃𝙾𝚁𝙰𝙶𝙴: <code>{}</code></b>
<b>᚛› 𝙵𝚁𝙴𝙴 𝚂𝚃𝙾𝚁𝙰𝙶𝙴: <code>{}</code></b>"""

# ================= Helper Functions =================

def humanbytes(size):
    if not size:
        return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'

def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return "%.2f %s" % (size, units[i])

async def run_shell_cmd(cmd, cwd):
    """Executes a shell command asynchronously and returns the status."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()

# ================= Handlers =================

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    current_time = datetime.now().strftime("%H:%M:%S")
    total, used, free = shutil.disk_usage(".")
    
    await message.reply(
        f"Welcome, {message.from_user.mention}! It's currently {current_time}.\n\n"
        f"🖥 **Server Disk Stats:**\n"
        f"Total: {humanbytes(total)}\n"
        f"Free: {humanbytes(free)}\n"
        f"Used: {humanbytes(used)}"
    )

@app.on_message(filters.command("mongo"))
async def mongo_stats(client, message):
    args = message.text.split()
    if len(args) < 4:
        return await message.reply("Usage: `/mongo <dburl> <dbname> <collection_name>`")
        
    dburl, dbname, collection_name = args[1], args[2], args[3]
    status_msg = await message.reply('<b>Processing🔰...</b>')
    
    try:
        # Use direct Motor queries (much faster, avoids uMongo registration errors)
        mongo = AsyncIOMotorClient(dburl)
        db = mongo[dbname]
        
        result = await db.command("dbstats")
        sizes = result.get('dataSize', 0)
        
        files = await db[collection_name].count_documents({})
        total_users = await db.users.count_documents({})
        totl_chats = await db.groups.count_documents({})
        
        size_str = get_size(sizes)
        # Assuming 512MB limit for free tier
        free_bytes = max(0, 536870912 - int(sizes))
        free_str = get_size(free_bytes)
        
        await status_msg.edit(Translation.STATUS_TXT.format(files, total_users, totl_chats, size_str, free_str))
    except Exception as e:
        await status_msg.edit(f"❌ Error: **{str(e)}**")


@app.on_message(filters.command("up", prefixes="/") & filters.reply)
async def upload_repo(client, message):
    replied_message = message.reply_to_message
    if not replied_message or not replied_message.document:
        return await message.reply_text("❌ Please reply to a valid ZIP file.")
        
    media = replied_message.document
    if not (media.file_name and media.file_name.lower().endswith('.zip')):
        return await message.reply_text("❌ The replied file must be a `.zip` archive.")

    # Create an isolated temporary directory for this specific upload
    temp_dir = tempfile.mkdtemp()
    file_path = os.path.join(temp_dir, "repo.zip")
    extract_path = os.path.join(temp_dir, "extracted")
    
    status_msg = await message.reply_text("📥 Downloading ZIP file...")
    
    try:
        # Download ZIP
        await client.download_media(media, file_name=file_path)
        
        # Token Prompt
        await status_msg.edit_text("🔑 Please enter your GitHub Personal Access Token (Timeout: 60s):")
        try:
            # Native asking handled by Pyroblack
            ak = await client.ask(message.from_user.id, "Enter token:", timeout=60)
        except (ListenerTimeout, asyncio.TimeoutError):
            return await message.reply_text("⏱ Request timed out. Try again.")
            
        token = ak.text.strip() if ak and ak.text else None
        if not token:
            return await message.reply_text("❌ Token required.")

        await status_msg.edit_text("🔐 Validating GitHub token...")
        
        # API Auth Check & Repo Creation
        g = Github(token)
        user = g.get_user()
        username = user.login
        
        # Extract File
        await status_msg.edit_text("📂 Extracting ZIP file...")
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(extract_path)
            
        # Determine the root target folder 
        extracted_entries = os.listdir(extract_path)
        if len(extracted_entries) == 1 and os.path.isdir(os.path.join(extract_path, extracted_entries[0])):
            nested_folder_path = os.path.join(extract_path, extracted_entries[0])
            folder_name = extracted_entries[0]
        else:
            nested_folder_path = extract_path
            folder_name = f"repo_{message.from_user.id}"

        # Clean folder name
        if not folder_name or folder_name.startswith('.'):
            folder_name = f"repo_{message.from_user.id}"

        await status_msg.edit_text(f"📤 Creating GitHub repository: `{folder_name}`...")
        
        try:
            repo = user.create_repo(folder_name, private=True)
        except GithubException as e:
            if e.status == 422:
                return await status_msg.edit_text(f"❌ Repository `{folder_name}` already exists on your account.")
            raise

        await status_msg.edit_text("🚀 Pushing files efficiently via Git...")

        # Construct Git authentication URL safely
        auth_url = f"https://{username}:{token}@github.com/{username}/{folder_name}.git"
        
        # Super-fast Subprocess Git Push (Bypasses API limits & sequential uploads)
        git_cmds = [
            "git init",
            'git config user.name "Telegram Bot"',
            'git config user.email "bot@telegram.org"',
            "git add .",
            'git commit -m "Initial commit via Telegram Bot"',
            "git branch -M main",
            f"git remote add origin {auth_url}",
            "git push -u origin main"
        ]
        
        for cmd in git_cmds:
            code, out, err = await run_shell_cmd(cmd, cwd=nested_folder_path)
            if code != 0 and "commit" not in cmd:  # Commit returns 1 if nothing to commit
                logger.error(f"Git error on `{cmd}`: {err}")
                return await status_msg.edit_text(f"❌ Git error occurred during upload.\n`{err[-200:]}`")

        # Success!
        repo_url = f"https://github.com/{username}/{folder_name}"
        await status_msg.edit_text(
            f"✅ **Repository uploaded successfully!**\n"
            f"🔗 **URL:** {repo_url}\n"
            f"⚡ *Uploaded using ultra-fast Git CLI.*",
            disable_web_page_preview=False
        )

    except BadCredentialsException:
        await status_msg.edit_text("❌ Invalid GitHub token.")
    except Exception as e:
        await status_msg.edit_text(f"❌ An error occurred: `{str(e)}`")
        logger.exception("Upload error")
    finally:
        # Guarantee removal of temporary session data
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

@app.on_message(filters.command("ext") & filters.private)
async def extract_and_upload(client, message):
    # /ext must be used as a reply to a document
    replied = message.reply_to_message

    if not replied or not replied.document:
        await message.reply_text("Reply to a document with /ext")
        return

    document = replied.document
    filename = document.file_name or "file"

    # Download the replied document
    local_path = await client.download_media(
        replied,
        file_name=filename
    )
    
    output_file = "output.txt"
    
    # Extract and save matches
    with open("log.txt", "r", encoding="utf-8") as f, \
         open(output_file, "w", encoding="utf-8") as out:
    
        for line in f:
            if "CARD_DECLINED" not in line:
                match = re.match(
                    r"(\d{16}\|\d{1,2}\|\d{1,2}\|\d{3,4})",
                    line
                )
    
                if match:
                    out.write(match.group(1) + "\n")
    
    print(f"Saved results to {output_file}")
    
# Delete the file when you're finished
# os.remove(output_file)

    try:
        # Upload with the same filename
        uploaded = await client.send_document(
            chat_id=message.chat.id,
            document=local_path,
            file_name=filename
        )

        # Delete /ext command, replied message, and downloaded local file
        await message.delete()
        await replied.delete()

    finally:
        # Always remove the local downloaded file
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
            

@app.on_message(filters.command("rename"))
async def rename_file(client, message):
    if not message.reply_to_message or len(message.command) < 2:
        return await message.reply_text("❌ Please reply to a file and provide the new name. \n`/rename new_name.mp4`")
        
    msg = message.reply_to_message
    new_name = " ".join(message.command[1:])
    
    # Extract file ID robustly based on type
    media = msg.document or msg.video or msg.audio or msg.photo or msg.animation
    if not media:
        return await message.reply_text("❌ No valid media found in the replied message.")
        
    status_msg = await message.reply_text("🔄 Renaming file...")
    # Fixed NameError by using the enums module directly imported at the top
    await client.send_chat_action(chat_id=message.chat.id, action=enums.ChatAction.UPLOAD_DOCUMENT)
    
    try:
        # Using send_document with a custom file_name bypasses the need to download/upload
        await client.send_document(
            chat_id=message.chat.id,
            document=media.file_id,
            file_name=new_name,
            caption=msg.caption
        )
        await status_msg.edit_text(f"✅ File renamed successfully!\n**New name:** `{new_name}`")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error renaming file: `{str(e)}`")


# ================= Background Keep-Alive Task =================

async def ping_server():
    sleep_time = 30
    url = "https://testclone-o3tl.onrender.com"
    while True:
        await asyncio.sleep(sleep_time)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as resp:
                    logger.info(f"Pinged server with response: {resp.status}")
        except Exception:
            logger.warning("Couldn't connect to the ping URL!")

# ================= Startup Routine =================

async def main():
    logger.info("Starting up bot...")
    await app.start()
    logger.info("Bot started! Hello Master Dhruv 🥳")
    
    # Run the background ping task concurrently
    asyncio.create_task(ping_server())
    
    await idle()
    await app.stop()
if __name__ == '__main__':
    try:
        # Fetch the existing event loop instead of creating a new one
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
"""
if __name__ == '__main__':
    try:
        # Modern way to run asyncio entrypoint
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
"""
