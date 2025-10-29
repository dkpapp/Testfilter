import asyncio
import aiohttp
import traceback
import math
import os
import time
import json
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import Message
import pyrogram
import pyromod
import logging
from umongo import Instance, Document, fields
import motor
from motor.motor_asyncio import AsyncIOMotorClient
import shutil
import os
import zipfile

from pyrogram.errors import BadRequest, FloodWait
from pyromod.exceptions import ListenerTimeout  # Added import here
from github import Github
from github.GithubException import BadCredentialsException, GithubException, RateLimitExceededException
#from github3 import GitHub

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
logit = logger.info
# Configuration
API_ID = int(os.environ.get("Api", 14604313)) # Replace with your API ID
API_HASH = os.environ.get("Hash", "a8ee65e5057b3f05cf9f28b71667203a")# Replace with your API hash
TOKEN = os.environ.get("token", "6602689172:AAHL3t4roHkQNxkF0H3fOcU2KByy6ryF48M")
bots = []
Tokens = {} # List to store cloned bot instances
class Translation:
    STATUS_TXT = """<b>᚛› 𝚃𝙾𝚃𝙰𝙻 𝙵𝙸𝙻𝙴𝚂: <code>{}</code></b>
<b>᚛› 𝚃𝙾𝚃𝙰𝙻 𝚄𝚂𝙴𝚁𝚂: <code>{}</code></b>
<b>᚛› 𝚃𝙾𝚃𝙰𝙻 𝙲𝙷𝙰𝚃𝚂: <code>{}</code></b>
<b>᚛› 𝚄𝚂𝙴𝙳 𝚂𝚃𝙾𝚁𝙰𝙶𝙴: <code>{}</code> 𝙼𝙱</b>
<b>᚛› 𝙵𝚁𝙴𝙴 𝚂𝚃𝙾𝚁𝙰𝙶𝙴: <code>{}</code> 𝙼𝙱</b>"""

def humanbytes(size):
    # https://stackoverflow.com/a/49361727/4723940
    # 2**10 = 1024
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
      while size >= 1024.0 and i < len(units):
          i += 1
          size /= 1024.0
      return "%.2f %s" % (size, units[i])

app = Client("main_bot", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN)

@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
      current_time = datetime.now().strftime("%H:%M:%S")
      total, used, free = shutil.disk_usage(".")
      total = humanbytes(total)
      used = humanbytes(used)
      free = humanbytes(free)
      await message.reply(f"Welcome, {message.from_user.mention}! It's currently {current_time}.\n Total : {total}\n Free: {free}\n Used: {used}")
"""
@app.on_message(filters.command("clone"))
async def clone(client, message):
    
      bot_token = message.text.split(" ")[1].strip()
      cloned_bot = Client("cloned_bot" + str(len(bots)), api_id=API_ID, api_hash=API_HASH, bot_token=bot_token)
      bots.append(cloned_bot)

      try:
            await cloned_bot.start()
            await message.reply_text("Bot cloned successfully!")
      except Exception as e:
            await message.reply_text("Error cloning bot: " + str(e))

@app.on_message(filters.command("clones"))
async def get_clones(client, message):
      await message.reply_text(f"Total cloned bots: {len(bots)}", quote=True)
"""
@app.on_message(filters.command("mongo"))
async def start(client, message):
      dburl = message.text.split(" ")[1]
      dbname = message.text.split(" ")[2]
      COLLECTION_NAME = message.text.split(" ")[3]
      rju = await message.reply('<b>Processing🔰...</b>')
      try:
        mongo = AsyncIOMotorClient(dburl)
        db = mongo[dbname]
        col = db.users
        grp = db.groups
        result = await db.command("dbstats")
        sizes = result['dataSize']
        #sizes = await db.command("dbstats")['dataSize']
      except Exception as e:
           await rju.edit(f"Error **{e}**")
      instance = Instance.from_db(db)
      @instance.register
      class Media(Document):
          file_id = fields.StrField(attribute='_id')
          file_ref = fields.StrField(allow_none=True)
          file_name = fields.StrField(required=True)
          file_size = fields.IntField(required=True)
          file_type = fields.StrField(allow_none=True)
          mime_type = fields.StrField(allow_none=True)
          caption = fields.StrField(allow_none=True)
          class Meta:
              collection_name = COLLECTION_NAME
      files = await Media.count_documents()
      size = get_size(sizes)
      free = 536870912 - int(sizes)
      free = get_size(free)
      total_users = await col.count_documents({})
      totl_chats = await grp.count_documents({})
      await rju.edit(Translation.STATUS_TXT.format(files, total_users, totl_chats, size, free))
##Functions


@app.on_message(filters.command("up", prefixes="/") & filters.reply)
async def upload_repo(client, message):
    status_msg = None
    file_path = None
    extract_path = "extracted_repo"
    
    try:
        replied_message = message.reply_to_message
        
        # Validate replied message has a document
        if not replied_message or not replied_message.document:
            await message.reply_text("❌ Please reply to a ZIP file.")
            return
        
        media = replied_message.document
        
        # Validate file type
        if not (media.file_name and media.file_name.lower().endswith('.zip')):
            await message.reply_text("❌ Please upload a valid ZIP file.")
            return
        
        # Download ZIP file with progress indication
        status_msg = await message.reply_text("📥 Downloading ZIP file...")
        file_path = await client.download_media(media, file_name="repo.zip")
        
        # GitHub token validation with retry
        max_token_attempts = 3
        token = None
        g = None
        
        for attempt in range(max_token_attempts):
            try:
                await status_msg.edit_text(f"🔑 Enter GitHub token (Attempt {attempt + 1}/{max_token_attempts}):")
                ak = await client.ask(
                    message.from_user.id, 
                    "Enter your GitHub Personal Access Token:",
                    timeout=60
                )
                
                if not ak or not ak.text or not ak.text.strip():
                    if attempt < max_token_attempts - 1:
                        await message.reply_text("⚠️ Token is required. Please try again.")
                        continue
                    else:
                        await message.reply_text("❌ Token is required. Upload cancelled.")
                        return
                
                token = ak.text.strip()
                
                # Validate token by attempting authentication
                await status_msg.edit_text("🔐 Validating GitHub token...")
                g = Github(token)
                g.get_user().login  # Test authentication
                await status_msg.edit_text("✅ Token validated successfully!")
                break
                
            except BadCredentialsException:
                if attempt < max_token_attempts - 1:
                    await message.reply_text(
                        f"❌ Invalid token. Please check your token and try again."
                        f"Attempts remaining: {max_token_attempts - attempt - 1}"
                    )
                    await asyncio.sleep(1)
                else:
                    await message.reply_text("❌ Authentication failed after 3 attempts. Please check your token.")
                    return
            except ListenerTimeout:
                await message.reply_text("⏱️ Request timed out. Please try again.")
                return
            except Exception as e:
                await message.reply_text(f"❌ Error during authentication: {str(e)}")
                return
        
        # Extract ZIP safely with path traversal protection
        await status_msg.edit_text("📂 Extracting ZIP file...")
        
        try:
            with zipfile.ZipFile(file_path, "r") as zip_ref:
                # Security check: validate all paths before extraction
                for member in zip_ref.namelist():
                    # Prevent path traversal attacks
                    member_path = os.path.normpath(os.path.join(extract_path, member))
                    if not member_path.startswith(os.path.abspath(extract_path)):
                        await message.reply_text("❌ Security Error: ZIP contains unsafe paths.")
                        return
                    
                    # Check uncompressed size to prevent zip bombs
                    info = zip_ref.getinfo(member)
                    if info.file_size > 500 * 1024 * 1024:  # 500 MB per file limit
                        await message.reply_text("❌ File too large. Maximum 500MB per file.")
                        return
                
                zip_ref.extractall(extract_path)
                
        except zipfile.BadZipFile:
            await message.reply_text("❌ Invalid or corrupted ZIP file.")
            return
        except Exception as e:
            await message.reply_text(f"❌ Extraction error: {str(e)}")
            return
        
        # Handle nested folders
        extracted_entries = os.listdir(extract_path)
        if len(extracted_entries) == 1 and os.path.isdir(os.path.join(extract_path, extracted_entries[0])):
            nested_folder_path = os.path.join(extract_path, extracted_entries[0])
        else:
            nested_folder_path = extract_path
        
        folder_name = os.path.basename(os.path.normpath(nested_folder_path))
        
        # Validate folder name for GitHub repository
        if not folder_name or folder_name.startswith('.'):
            folder_name = f"repo_{message.from_user.id}"
        
        # Create repository with error handling
        await status_msg.edit_text(f"📤 Creating repository: {folder_name}...")
        
        try:
            repo = g.get_user().create_repo(folder_name, private=True)
        except GithubException as e:
            if e.status == 422:  # Repository already exists
                await message.reply_text(f"❌ Repository '{folder_name}' already exists. Please rename and try again.")
            else:
                await message.reply_text(f"❌ Failed to create repository: {e.data.get('message', str(e))}")
            return
        
        # Upload files with rate limit handling
        await status_msg.edit_text("📤 Uploading files to repository...")
        
        uploaded_count = 0
        failed_count = 0
        
        for root, _, files in os.walk(nested_folder_path):
            for file in files:
                # Skip backup and system files
                if file.endswith(('.bak', '.tmp')) or file.startswith('.'):
                    continue
                
                filepath = os.path.join(root, file)
                relative_path = os.path.relpath(filepath, nested_folder_path)
                
                # Skip files larger than 100MB (GitHub limit)
                if os.path.getsize(filepath) > 100 * 1024 * 1024:
                    failed_count += 1
                    continue
                
                try:
                    with open(filepath, "rb") as f:
                        content = f.read()
                        repo.create_file(
                            relative_path, 
                            f"Add {relative_path}", 
                            content
                        )
                    uploaded_count += 1
                    
                    # Update progress every 10 files
                    if uploaded_count % 10 == 0:
                        await status_msg.edit_text(f"📤 Uploaded {uploaded_count} files...")
                    
                except RateLimitExceededException as e:
                    # Wait for rate limit reset
                    reset_time = e.headers.get('X-RateLimit-Reset', 60)
                    wait_time = int(reset_time) - int(asyncio.get_event_loop().time())
                    await status_msg.edit_text(f"⏳ Rate limit reached. Waiting {wait_time}s...")
                    await asyncio.sleep(wait_time + 1)
                    
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    
                except GithubException as e:
                    if e.status == 422:  # File already exists
                        continue
                    failed_count += 1
                    
                except Exception:
                    failed_count += 1
                    continue
        
        # Success notification
        repo_url = f"https://github.com/{g.get_user().login}/{folder_name}"
        success_msg = (
            f"✅ **Repository uploaded successfully!**"
            f"🔗 **URL:** {repo_url}"
            f"📁 **Files uploaded:** {uploaded_count}"
        )
        
        if failed_count > 0:
            success_msg += f"⚠️ **Failed:** {failed_count} files"
        
        await message.reply_text(success_msg, disable_web_page_preview=False)
        
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await message.reply_text(f"⏳ Telegram rate limit. Please wait {e.value} seconds.")
        
    except BadRequest as e:
        await message.reply_text(f"❌ Bad request: {str(e)}")
        
    except Exception as e:
        error_msg = f"❌ An unexpected error occurred: {str(e)}"
        await message.reply_text(error_msg)
        
    finally:
        # Cleanup: Remove downloaded and extracted files
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            if os.path.exists(extract_path):
                shutil.rmtree(extract_path)
        except Exception:
            pass  # Silent cleanup failure

"""
@app.on_message(filters.command("up", prefixes="/") & filters.reply)
async def upload_repo(client, message):
    try:
        # Get the replied-to message containing the ZIP file
        replied_message = message.reply_to_message
        media = replied_message.document

        # Download the ZIP file locally
        file_path = await client.download_media(media, file_name="repo.zip")
        #if Tokens.get(message.from_user.id, None) is None:
        #if
        ak = await client.ask(message.from_user.id, "Enter gh token:")
        if ak.text:
                 xy = str(ak.text)
                #Tokens[message.from_user.id] = ak.text
        #xy = str(Tokens.get(message.from_user.id))
        g = GitHub(token=xy)
        # Extract the ZIP file
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall("extracted_repo")

        # Get the extracted folder name
        nested_folder_path = os.path.join("extracted_repo", os.listdir("extracted_repo")[0])#next(os.walk("extracted_repo"))[1][0])  # Get the first subfolder
        folder_name = os.path.basename(os.path.normpath(nested_folder_path))
        second_subfolder = os.path.join(nested_folder_path, os.listdir(nested_folder_path)[0])
        #logging.info(f"Nested : {nested_folder_path}")
        #folder_name = os.path.basename(second_subfolder)
        # Create a new GitHub repository with the same name
        #user = g.get_user()  # Get the authenticated user
        repo = g.create_repository(folder_name, private=True)

        # Add, commit, and push the files to the new repository
        #repo.create_file("README.md", "Initial commit", "")  # Add a basic README
        for root, _, files in os.walk(nested_folder_path):
            for file in files:
                if file.endswith(".bak"):
                    continue
                filepath = os.path.join(root, file)
                relative_path = os.path.relpath(filepath, nested_folder_path)#.replace(nested_folder_path + "/", "")#f"./extracted_repo/{folder_name}/{file}"
                with open(filepath, "rb") as f:
                     repo.create_file(relative_path, "main", f.read())

        # Notify the user about successful upload
        await message.reply_text("Repository uploaded successfully: https://github.com/{m}/{repo}".format(m=g.me().login, repo=folder_name))
        try:
          os.remove(file_path)
        except:
          pass
        shutil.rmtree("extracted_repo")

    except Exception as e:
        await message.reply_text("An error occurred: {}".format(str(e)))
"""
@app.on_message(filters.command(["rename"]))
async def rename_file(client, message):
    #"""Renames a file without downloading and sends it back."""
    try:
        # Extract file information with detailed comments
        file_id = message.reply_to_message.video # Get file ID from the replied-to message
        original_file_name = message.reply_to_message.video.file_name  # Get original file name
        new_name = message.command[1]  # Extract new name from command arguments

        # Inform the user about the process
        await client.send_chat_action(chat_id=message.chat.id, action="typing")  # Indicate bot activity
        await client.send_message(chat_id=message.chat.id, text="Renaming file...")

        # Workaround to create InputMediaDocument without v1 types
        #media = await client.get_document(file_id)  # Get media information
        input_media = pyrogram.InputMediaDocument(file_id, new_name, caption=media.caption)  # Create InputMedia

        # Send the renamed file using send_media
        await client.send_media(chat_id=message.chat.id, media=input_media)

        # Send a confirmation message
        await client.send_message(chat_id=message.chat.id, text=f"File renamed successfully! New name: {new_name}")

    except Exception as e:
        # Handle errors gracefully with a user-friendly message and logging
        await client.send_message(chat_id=message.chat.id, text=f"An error occurred while renaming the file. Please try again later.\n **{e}**")
        print(f"Error renaming file: {e}")  # Log error for debugging

async def ping_server():
    sleep_time = 40#300
    url = "https://testclone-o3tl.onrender.com"
    while True:
        await asyncio.sleep(sleep_time)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url) as resp:
                    logging.info("Pinged server with response: {}".format(resp.status))
        except TimeoutError:
            logging.warning("Couldn't connect to the site URL..!")
        except Exception:
            traceback.print_exc()

async def main():
    await app.start()
    logit("Hello Master Dhruv 🥳")
    asyncio.create_task(ping_server())
    await idle()
    #await asyncio.gather(*[await bot.start() for bot in bots])
    
if __name__ == '__main__':
    #asyncio.run(main())
    asyncio.get_event_loop().run_until_complete(main())
    
