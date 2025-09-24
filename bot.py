import os
import json
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ===== LOGGING SETUP =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== CONFIG =====
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
# Railway automatically provides PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
DATABASE_URL = os.environ.get("DATABASE_URL")

# If no DATABASE_URL, construct from Railway environment variables
if not DATABASE_URL:
    PGHOST = os.environ.get("PGHOST")
    PGPORT = os.environ.get("PGPORT", "5432")
    PGDATABASE = os.environ.get("PGDATABASE")
    PGUSER = os.environ.get("PGUSER")
    PGPASSWORD = os.environ.get("PGPASSWORD")
    
    if all([PGHOST, PGDATABASE, PGUSER, PGPASSWORD]):
        DATABASE_URL = f"postgresql://{PGUSER}:{PGPASSWORD}@{PGHOST}:{PGPORT}/{PGDATABASE}"

# ===== DATABASE SETUP =====
class DatabaseManager:
    def __init__(self):
        self.conn = None
        self.connect()
        self.create_tables()

    def connect(self):
        """Connect to PostgreSQL database"""
        try:
            # Railway/Render require SSL
            self.conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            self.conn.autocommit = True  # Auto-commit for better reliability
            logger.info("✅ Connected to PostgreSQL database")
        except Exception as e:
            logger.error(f"❌ Database connection error: {e}")
            # Fallback: try without SSL for local development
            try:
                self.conn = psycopg2.connect(DATABASE_URL)
                self.conn.autocommit = True
                logger.info("✅ Connected to PostgreSQL database (no SSL)")
            except Exception as e2:
                logger.error(f"❌ Database connection failed completely: {e2}")
                raise

    def create_tables(self):
        """Create tables if they don't exist"""
        try:
            with self.conn.cursor() as cur:
                # Table for folder structure
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS folders (
                        id SERIAL PRIMARY KEY,
                        path TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        parent_path TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Table for files
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS files (
                        id SERIAL PRIMARY KEY,
                        filename TEXT NOT NULL,
                        folder_path TEXT NOT NULL,
                        file_id TEXT NOT NULL,
                        file_type TEXT DEFAULT 'document',
                        file_size BIGINT DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(filename, folder_path)
                    )
                ''')
                
                # Create indexes for better performance
                cur.execute('CREATE INDEX IF NOT EXISTS idx_folders_parent_path ON folders(parent_path)')
                cur.execute('CREATE INDEX IF NOT EXISTS idx_files_folder_path ON files(folder_path)')
                
                # Create root folder if it doesn't exist
                cur.execute('''
                    INSERT INTO folders (path, name, parent_path) 
                    VALUES ('/', 'Root', NULL) 
                    ON CONFLICT (path) DO NOTHING
                ''')
                
                logger.info("✅ Database tables created/verified successfully")
        except Exception as e:
            logger.error(f"❌ Error creating tables: {e}")
            raise

    def get_folder_structure(self, path='/'):
        """Get folder structure from database"""
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get subfolders
                cur.execute('''
                    SELECT name FROM folders 
                    WHERE parent_path = %s 
                    ORDER BY name
                ''', (path,))
                subfolders = {row['name']: {} for row in cur.fetchall()}
                
                # Get files
                cur.execute('''
                    SELECT filename, file_id FROM files 
                    WHERE folder_path = %s 
                    ORDER BY filename
                ''', (path,))
                files = {row['filename']: row['file_id'] for row in cur.fetchall()}
                
                return {
                    'subfolders': subfolders,
                    'files': files
                }
        except Exception as e:
            logger.error(f"❌ Error getting folder structure: {e}")
            return {'subfolders': {}, 'files': {}}

    def create_folder(self, parent_path, folder_name):
        """Create a new folder"""
        try:
            new_path = f"{parent_path.rstrip('/')}/{folder_name}"
            if parent_path == '/':
                new_path = f"/{folder_name}"
                
            with self.conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO folders (path, name, parent_path) 
                    VALUES (%s, %s, %s)
                ''', (new_path, folder_name, parent_path))
                logger.info(f"✅ Created folder: {new_path}")
                return True
        except psycopg2.IntegrityError:
            logger.warning(f"⚠️ Folder already exists: {folder_name}")
            return False  # Folder already exists
        except Exception as e:
            logger.error(f"❌ Error creating folder: {e}")
            return False

    def delete_folder(self, parent_path, folder_name):
        """Delete a folder and all its contents"""
        try:
            folder_path = f"{parent_path.rstrip('/')}/{folder_name}"
            if parent_path == '/':
                folder_path = f"/{folder_name}"
                
            with self.conn.cursor() as cur:
                # Delete all files in this folder and subfolders
                cur.execute('''
                    DELETE FROM files 
                    WHERE folder_path LIKE %s
                ''', (f"{folder_path}%",))
                
                # Delete all subfolders
                cur.execute('''
                    DELETE FROM folders 
                    WHERE path LIKE %s
                ''', (f"{folder_path}%",))
                
                logger.info(f"✅ Deleted folder and contents: {folder_path}")
                return True
        except Exception as e:
            logger.error(f"❌ Error deleting folder: {e}")
            return False

    def add_file(self, folder_path, filename, file_id, file_type='document', file_size=0):
        """Add a file to the database"""
        try:
            with self.conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO files (filename, folder_path, file_id, file_type, file_size) 
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (filename, folder_path) 
                    DO UPDATE SET 
                        file_id = EXCLUDED.file_id, 
                        file_type = EXCLUDED.file_type,
                        file_size = EXCLUDED.file_size,
                        created_at = CURRENT_TIMESTAMP
                ''', (filename, folder_path, file_id, file_type, file_size))
                logger.info(f"✅ Added file: {filename} to {folder_path}")
                return True
        except Exception as e:
            logger.error(f"❌ Error adding file: {e}")
            return False

    def delete_file(self, folder_path, filename):
        """Delete a file"""
        try:
            with self.conn.cursor() as cur:
                cur.execute('''
                    DELETE FROM files 
                    WHERE filename = %s AND folder_path = %s
                ''', (filename, folder_path))
                logger.info(f"✅ Deleted file: {filename} from {folder_path}")
                return True
        except Exception as e:
            logger.error(f"❌ Error deleting file: {e}")
            return False

    def get_file_id(self, folder_path, filename):
        """Get file ID for download"""
        try:
            with self.conn.cursor() as cur:
                cur.execute('''
                    SELECT file_id FROM files 
                    WHERE filename = %s AND folder_path = %s
                ''', (filename, folder_path))
                result = cur.fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.error(f"❌ Error getting file ID: {e}")
            return None

    def get_stats(self):
        """Get database statistics"""
        try:
            with self.conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM folders WHERE path != %s', ('/',))
                folder_count = cur.fetchone()[0]
                
                cur.execute('SELECT COUNT(*) FROM files')
                file_count = cur.fetchone()[0]
                
                cur.execute('SELECT SUM(file_size) FROM files')
                total_size = cur.fetchone()[0] or 0
                
                return folder_count, file_count, total_size
        except Exception as e:
            logger.error(f"❌ Error getting stats: {e}")
            return 0, 0, 0

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("🔐 Database connection closed")

# Initialize database
try:
    db = DatabaseManager()
except Exception as e:
    logger.error(f"❌ Failed to initialize database: {e}")
    db = None

# ===== STORAGE =====
user_paths = {}  # track user navigation {user_id: ["Folder1", ...]}
upload_context = {}  # track where admin is uploading {user_id: path_list}

# ===== HELPERS =====
def path_to_string(path_list):
    """Convert path list to string"""
    if not path_list:
        return '/'
    return '/' + '/'.join(path_list)

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.1f} {size_names[i]}"

async def safe_edit_message(query, text, reply_markup=None):
    """Safely edit message with error handling for 'Message is not modified' error"""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as e:
        if "Message is not modified" in str(e):
            # Message content is the same, just answer callback to remove loading state
            await query.answer("✅ Already up to date!")
        elif "Message can't be edited" in str(e):
            # Message is too old to edit, send new message
            await query.message.reply_text(text, reply_markup=reply_markup)
        else:
            logger.error(f"❌ Error editing message: {e}")
            await query.answer("❌ Error updating interface", show_alert=True)

def add_back_button(buttons: list) -> InlineKeyboardMarkup:
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
    buttons.append([InlineKeyboardButton("🧹 Clear Interface", callback_data="clear_interface")])
    return InlineKeyboardMarkup(buttons)

def main_menu_buttons(is_admin: bool) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("📂 Browse Folders", callback_data="browse_folders")]]
    if is_admin:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_main")])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="close_interface")])
    keyboard.append([InlineKeyboardButton("🧹 Clear Interface", callback_data="clear_interface")])
    return InlineKeyboardMarkup(keyboard)

def build_folder_buttons(folder_data: dict, is_admin=False):
    """Build folder navigation buttons"""
    buttons = []
    
    # Add subfolder buttons
    for name in sorted(folder_data.get("subfolders", {})):
        if name and len(name.strip()) > 0:
            buttons.append([InlineKeyboardButton(f"📁 {name[:50]}", callback_data=f"open_folder|{name}")])
    
    # Add file buttons
    for filename in sorted(folder_data.get("files", {})):
        if filename and len(filename.strip()) > 0:
            display_name = filename[:50] + "..." if len(filename) > 50 else filename
            buttons.append([InlineKeyboardButton(f"📄 {display_name}", callback_data=f"download|{filename}")])
    
    if is_admin:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_current")])
    
    return buttons

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_paths[user.id] = []
    is_admin = user.username == ADMIN_USERNAME
    
    if not db:
        await update.message.reply_text("❌ Database connection failed. Please contact admin.")
        return
    
    platform = "🚂 Railway" if "railway" in os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").lower() else "☁️ Cloud"
    
    await update.message.reply_text(
        f"📁 **Cybersecurity Lectures Bot**\n\n"
        f"🚀 Designed By Team OP\n"
        f"💾 **From A ---> Z**\n\n"
        f"\n"
        f"OPH.",
        reply_markup=main_menu_buttons(is_admin),
        parse_mode='Markdown'
    )

# ===== BUTTON HANDLER =====
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not db:
        await query.answer("❌ Database unavailable", show_alert=True)
        return
    
    # ---- CLOSE INTERFACE ----
    if query.data == "close_interface":
        try:
            await query.message.delete()
        except Exception as e:
            logger.warning(f"Could not delete message: {e}")
        return
    
    user = query.from_user
    is_admin = user.username == ADMIN_USERNAME
    path = user_paths.get(user.id, [])
    current_path = path_to_string(path)

    # ---- BACK ----
    if query.data == "back":
        if path:
            path.pop()
            user_paths[user.id] = path
            current_path = path_to_string(path)
            
        if path:
            folder_data = db.get_folder_structure(current_path)
            buttons = build_folder_buttons(folder_data, is_admin=is_admin)
            path_display = " > ".join(path) if path else "Root"
            await safe_edit_message(query, f"📂 Current Folder: {path_display}", add_back_button(buttons))
        else:
            await safe_edit_message(query, "📁 Main Menu", main_menu_buttons(is_admin))
        return

    # ---- CLEAR INTERFACE ----
    if query.data == "clear_interface":
        chat_id = query.message.chat_id
        # Delete recent messages
        for message_id in range(query.message.message_id, max(1, query.message.message_id - 10), -1):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except:
                pass
        
        await context.bot.send_message(
            chat_id, 
            "🧹 Interface cleared.", 
            reply_markup=main_menu_buttons(is_admin)
        )
        return

    # ---- BROWSE ROOT ----
    if query.data == "browse_folders":
        user_paths[user.id] = []
        folder_data = db.get_folder_structure('/')
        buttons = build_folder_buttons(folder_data, is_admin=is_admin)
        await safe_edit_message(query, "📂 Root Folders:", add_back_button(buttons))
        return

    # ---- OPEN FOLDER ----
    if query.data.startswith("open_folder|"):
        folder_name = query.data.split("|", 1)[1]
        
        path.append(folder_name)
        user_paths[user.id] = path
        new_path = path_to_string(path)
        
        folder_data = db.get_folder_structure(new_path)
        buttons = build_folder_buttons(folder_data, is_admin=is_admin)
        await safe_edit_message(query, f"📂 {folder_name}:", add_back_button(buttons))
        return

    # ---- DOWNLOAD FILE ----
    if query.data.startswith("download|"):
        filename = query.data.split("|", 1)[1]
        file_id = db.get_file_id(current_path, filename)
        
        if file_id:
            try:
                await query.message.reply_document(file_id, caption=f"📄 {filename}")
                logger.info(f"✅ File downloaded: {filename}")
            except Exception as e:
                logger.error(f"❌ Error sending file {filename}: {e}")
                await query.answer("❌ Error downloading file", show_alert=True)
        else:
            await query.answer("❌ File not found", show_alert=True)
        return

    # Admin-only actions
    if not is_admin:
        await query.answer("⛔ Admin access required", show_alert=True)
        return

    # ---- ADMIN MAIN PANEL ----
    if query.data == "admin_main":
        buttons = [
            [InlineKeyboardButton("📂 Browse & Manage", callback_data="browse_folders")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        ]
        await safe_edit_message(query, "⚙️ Admin Main Panel", add_back_button(buttons))
        return

    # ---- ADMIN STATS ----
    if query.data == "admin_stats":
        folder_count, file_count, total_size = db.get_stats()
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        platform = "🚂 Railway" if "railway" in os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").lower() else "☁️ Cloud"
        
        stats_text = (
            f"📊 **Bot Statistics** (Updated: {timestamp}):\n\n"
            f"📁 Total Folders: **{folder_count}**\n"
            f"📄 Total Files: **{file_count}**\n"
            f"💾 Total Size: **{format_file_size(total_size)}**\n"
            f"🗄️ Database: **PostgreSQL (Persistent)**\n"
            f"🌐 Platform: **{platform}**"
        )
        
        buttons = [[InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats")]]
        await safe_edit_message(query, stats_text, add_back_button(buttons))
        return

    # ---- ADMIN CURRENT FOLDER PANEL ----
    if query.data == "admin_current":
        buttons = [
            [InlineKeyboardButton("📁 Create Folder", callback_data="create_folder_current")],
            [InlineKeyboardButton("📤 Upload File", callback_data="upload_current")],
            [InlineKeyboardButton("❌ Delete Folder", callback_data="delete_folder_current")],
            [InlineKeyboardButton("🗑️ Delete File", callback_data="delete_file_current")]
        ]
        path_display = " > ".join(path) if path else "Root"
        await safe_edit_message(query, f"⚙️ Admin Panel\n📍 Current: {path_display}", add_back_button(buttons))
        return

    # ---- CREATE FOLDER ----
    if query.data == "create_folder_current":
        context.user_data["awaiting_folder_name"] = True
        context.user_data["folder_path"] = path.copy()
        await safe_edit_message(query, "✏️ Send the name for the new folder:")
        return

    # ---- UPLOAD FILE ----
    if query.data == "upload_current":
        upload_context[user.id] = path.copy()
        await safe_edit_message(query, "📤 Now send the file to upload into this folder.")
        return

    # ---- DELETE FOLDER MENU ----
    if query.data == "delete_folder_current":
        folder_data = db.get_folder_structure(current_path)
        subfolders = folder_data.get("subfolders", {})
        
        if not subfolders:
            await safe_edit_message(query, "⚠️ No subfolders to delete.", add_back_button([]))
            return
            
        buttons = []
        for name in sorted(subfolders.keys()):
            display_name = name[:40] + "..." if len(name) > 40 else name
            buttons.append([InlineKeyboardButton(f"🗑️ {display_name}", callback_data=f"delete_folder_select|{name}")])
            
        await safe_edit_message(query, "🗑️ Select a folder to delete:", add_back_button(buttons))
        return

    # ---- DELETE SELECTED FOLDER ----
    if query.data.startswith("delete_folder_select|"):
        folder_name = query.data.split("|", 1)[1]
        
        if db.delete_folder(current_path, folder_name):
            await safe_edit_message(query, f"✅ Folder '{folder_name}' deleted successfully.", add_back_button([]))
        else:
            await safe_edit_message(query, "❌ Error deleting folder.", add_back_button([]))
        return

    # ---- DELETE FILE MENU ----
    if query.data == "delete_file_current":
        folder_data = db.get_folder_structure(current_path)
        files = folder_data.get("files", {})
        
        if not files:
            await safe_edit_message(query, "⚠️ No files to delete.", add_back_button([]))
            return
        
        buttons = []
        for filename in sorted(files.keys()):
            display_name = filename[:40] + "..." if len(filename) > 40 else filename
            buttons.append([InlineKeyboardButton(f"🗑️ {display_name}", callback_data=f"delete_file_select|{filename}")])
        
        await safe_edit_message(query, "🗑️ Select a file to delete:", add_back_button(buttons))
        return

    # ---- DELETE SELECTED FILE ----
    if query.data.startswith("delete_file_select|"):
        filename = query.data.split("|", 1)[1]
        
        if db.delete_file(current_path, filename):
            await safe_edit_message(query, f"✅ File '{filename}' deleted successfully.", add_back_button([]))
        else:
            await safe_edit_message(query, "❌ Error deleting file.", add_back_button([]))
        return

# ===== HANDLE TEXT (FOLDER NAMES) =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username != ADMIN_USERNAME:
        return
        
    if not db:
        await update.message.reply_text("❌ Database unavailable.")
        return
        
    if context.user_data.get("awaiting_folder_name"):
        name = update.message.text.strip()
        
        # Validate folder name
        if not name or len(name) > 100:
            await update.message.reply_text("⚠️ Folder name must be between 1-100 characters.")
            return
            
        # Check for invalid characters
        invalid_chars = ['|', '/', '\\', ':', '*', '?', '"', '<', '>', '\n', '\r']
        if any(char in name for char in invalid_chars):
            await update.message.reply_text("⚠️ Folder name contains invalid characters.")
            return
            
        path = context.user_data.get("folder_path", [])
        parent_path = path_to_string(path)
        
        if db.create_folder(parent_path, name):
            await update.message.reply_text(f"✅ Folder '{name}' created successfully.")
        else:
            await update.message.reply_text("⚠️ Folder already exists or error occurred.")
                
        context.user_data["awaiting_folder_name"] = False

# ===== HANDLE FILE UPLOADS =====
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username != ADMIN_USERNAME:
        await update.message.reply_text("⛔ Not authorized to upload files.")
        return

    if not db:
        await update.message.reply_text("❌ Database unavailable.")
        return

    path = upload_context.get(user.id, user_paths.get(user.id, []))
    folder_path = path_to_string(path)

    try:
        filename = None
        file_id = None
        file_type = "document"
        file_size = 0
        
        if update.message.document:
            file = update.message.document
            filename = file.file_name or f"document_{file.file_unique_id}"
            file_id = file.file_id
            file_type = "document"
            file_size = file.file_size or 0
            
        elif update.message.photo:
            photo = update.message.photo[-1]
            filename = f"photo_{photo.file_unique_id}.jpg"
            file_id = photo.file_id
            file_type = "photo"
            file_size = photo.file_size or 0
            
        elif update.message.video:
            video = update.message.video
            filename = f"video_{video.file_unique_id}.mp4"
            file_id = video.file_id
            file_type = "video"
            file_size = video.file_size or 0
            
        elif update.message.audio:
            audio = update.message.audio
            filename = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
            file_id = audio.file_id
            file_type = "audio"
            file_size = audio.file_size or 0
            
        else:
            await update.message.reply_text("❌ Unsupported file type.")
            return

        # Validate filename
        if not filename or len(filename) > 200:
            filename = f"file_{file_id[:10]}"

        # Add file to database
        if db.add_file(folder_path, filename, file_id, file_type, file_size):
            path_display = " > ".join(path) if path else "Root"
            size_str = format_file_size(file_size)
            await update.message.reply_text(
                f"✅ File uploaded successfully!\n\n"
                f"📄 **{filename}**\n"
                f"📍 Location: {path_display}\n"
                f"📊 Size: {size_str}\n"
                f"🏷️ Type: {file_type}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Error uploading file to database.")
        
        # Clear upload context
        upload_context.pop(user.id, None)
        
    except Exception as e:
        logger.error(f"❌ Error uploading file: {e}")
        await update.message.reply_text(f"❌ Error uploading file: {str(e)}")

# ===== ERROR HANDLER =====
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify admin"""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An error occurred. Please try again or contact admin."
            )
        except:
            pass

# ===== MAIN =====
def main():
    if not TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required")
        return
        
    if not ADMIN_USERNAME:
        logger.error("❌ ADMIN_USERNAME environment variable is required")
        return
        
    if not DATABASE_URL:
        logger.error("❌ DATABASE_URL or PostgreSQL environment variables are required")
        return

    if not db:
        logger.error("❌ Database connection failed - cannot start bot")
        return

    try:
        app = Application.builder().token(TOKEN).build()
        
        # Add handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(button))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        app.add_handler(MessageHandler(
            (filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO) & ~filters.COMMAND, 
            handle_file
        ))
        
        # Add error handler
        app.add_error_handler(error_handler)
        
        # Detect platform
        platform = "Railway" if os.environ.get("RAILWAY_ENVIRONMENT_NAME") else "Cloud Platform"
        logger.info(f"✅ File Manager Bot starting on {platform} with PostgreSQL persistence...")
        
        # Start polling
        app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
    finally:
        # Close database connection on shutdown
        if db:
            db.close()

if __name__ == "__main__":
    main()

