import os
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ===== CONFIG =====
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
DB_FILE = "folders.json"

# ===== STORAGE =====
if os.path.exists(DB_FILE):
    with open(DB_FILE, "r", encoding="utf-8") as f:
        folder_storage = json.load(f)
else:
    folder_storage = {}  # {"FolderName": {"files": {}, "subfolders": {}}}

user_paths = {}  # track user navigation {user_id: ["Folder1", ...]}
upload_context = {}  # track where admin is uploading {user_id: path_list}

# ===== HELPERS =====
def save_data():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(folder_storage, f, indent=2, ensure_ascii=False)


def get_current_folder(path):
    folder = folder_storage
    for p in path:
        folder = folder["subfolders"][p]
    return folder


def add_back_button(buttons: list) -> InlineKeyboardMarkup:
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
    buttons.append([InlineKeyboardButton("🧹 Clear Interface", callback_data="clear_interface")])
    return InlineKeyboardMarkup(buttons)

def add_clear_button(buttons: list) -> InlineKeyboardMarkup:
    buttons.append([InlineKeyboardButton("🧹 Clear Interface", callback_data="clear_interface")])
    return add_clear_button(buttons)


def main_menu_buttons(is_admin: bool) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("📂 Browse Folders", callback_data="browse_folders")]]
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="close_interface")])
    keyboard.append([InlineKeyboardButton("🧹 Clear Interface", callback_data="clear_interface")])
    return InlineKeyboardMarkup(keyboard)


def build_folder_buttons(folder: dict, is_admin=False):
    buttons = []
    for name in folder.get("subfolders", {}):
        buttons.append([InlineKeyboardButton(f"📁 {name}", callback_data=f"open_folder|{name}")])
    for filename in folder.get("files", {}):
        buttons.append([InlineKeyboardButton(f"📄 {filename}", callback_data=f"download|{filename}")])
    if is_admin:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_current")])
    return buttons


# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_paths[user.id] = []
    is_admin = user.username == ADMIN_USERNAME
    await update.message.reply_text(
        "📁 Welcome to Cybersecurity lectures Bot",
        reply_markup=main_menu_buttons(is_admin)
    )


# ===== BUTTON HANDLER =====
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # ---- CLOSE INTERFACE ----
    if query.data == "close_interface":
        chat_id = query.message.chat_id
        try:
            await query.message.delete()  # delete the current menu message
        except:
            pass  # ignore if already deleted
        return
    user = query.from_user
    is_admin = user.username == ADMIN_USERNAME
    path = user_paths.get(user.id, [])

    # ---- BACK ----
    if query.data == "back":
        if path:
            path.pop()
            user_paths[user.id] = path
            folder = get_current_folder(path) if path else folder_storage
            buttons = build_folder_buttons(folder, is_admin=is_admin)
            await query.edit_message_text("📂 Current Folder:", reply_markup=add_back_button(buttons))
        else:
            await query.edit_message_text("📁 Main Menu", reply_markup=main_menu_buttons(is_admin))
        return

    # ---- CLEAR INTERFACE ----
    if query.data == "clear_interface":
      chat_id = query.message.chat_id
      # Attempt to delete last 50 messages sent by the bot
      for message_id in range(query.message.message_id, query.message.message_id - 10, -1):
          try:
              await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
          except:
              pass
      # Send a fresh main menu
      is_admin = query.from_user.username == ADMIN_USERNAME
      await context.bot.send_message(chat_id, "🧹 Interface cleared.", reply_markup=main_menu_buttons(is_admin))
      return


    # ---- BROWSE ROOT ----
    if query.data == "browse_folders":
        user_paths[user.id] = []
        folder = folder_storage
        buttons = build_folder_buttons(folder, is_admin=is_admin)
        await query.edit_message_text("📂 Root Folders:", reply_markup=add_back_button(buttons))
        return

    # ---- OPEN FOLDER ----
    if query.data.startswith("open_folder|"):
        folder_name = query.data.split("|")[1]
        folder = get_current_folder(path) if path else folder_storage
        if folder_name not in folder.get("subfolders", {}):
            await query.answer("❌ Folder not found", show_alert=True)
            return
        path.append(folder_name)
        user_paths[user.id] = path
        folder = folder["subfolders"][folder_name]
        buttons = build_folder_buttons(folder, is_admin=is_admin)
        await query.edit_message_text(f"📂 {folder_name}:", reply_markup=add_back_button(buttons))
        return

    # ---- DOWNLOAD FILE ----
    if query.data.startswith("download|"):
        filename = query.data.split("|")[1]
        folder = get_current_folder(path) if path else folder_storage
        file_id = folder.get("files", {}).get(filename)
        if file_id:
            await query.message.reply_document(file_id, caption=f"📄 {filename}")
        else:
            await query.answer("❌ File not found", show_alert=True)
        return

    # ---- ADMIN PANEL ----
    if query.data == "admin_current" and is_admin:
        folder = get_current_folder(path) if path else folder_storage
        buttons = [
            [InlineKeyboardButton("📁 Create Folder", callback_data="create_folder_current")],
            [InlineKeyboardButton("📤 Upload File", callback_data="upload_current")],
            [InlineKeyboardButton("❌ Delete Folder", callback_data="delete_folder_current")],
            [InlineKeyboardButton("🗑️ Delete File", callback_data="delete_file_current")]
        ]
        await query.edit_message_text("⚙️ Admin Panel (Current Folder)", reply_markup=add_back_button(buttons))
        return

    # ---- CREATE FOLDER ----
    if query.data == "create_folder_current" and is_admin:
        context.user_data["awaiting_folder_name"] = True
        context.user_data["folder_path"] = path.copy()
        await query.edit_message_text("✏️ Send the name for the new folder in this folder:")
        return

    # ---- UPLOAD FILE ----
    if query.data == "upload_current" and is_admin:
        upload_context[user.id] = path.copy()
        await query.edit_message_text("📤 Now send the file to upload into this folder.")
        return

    # ---- DELETE FOLDER MENU ----
    if query.data == "delete_folder_current" and is_admin:
        folder = get_current_folder(path) if path else folder_storage
        subfolders = folder.get("subfolders", {})
        if not subfolders:
            await query.edit_message_text("⚠️ No subfolders to delete.", reply_markup=add_back_button([]))
            return
        buttons = [[InlineKeyboardButton(name, callback_data=f"delete_folder_select|{name}")] for name in subfolders]
        await query.edit_message_text("🗑️ Select a folder to delete:", reply_markup=add_back_button(buttons))
        return

    # ---- DELETE SELECTED FOLDER ----
    if query.data.startswith("delete_folder_select|") and is_admin:
        folder_name = query.data.split("|")[1]
        parent_folder = get_current_folder(path) if path else folder_storage
        if folder_name in parent_folder.get("subfolders", {}):
            del parent_folder["subfolders"][folder_name]
            save_data()
            buttons = [
                [InlineKeyboardButton("📁 Create Folder", callback_data="create_folder_current")],
                [InlineKeyboardButton("📤 Upload File", callback_data="upload_current")],
                [InlineKeyboardButton("❌ Delete Folder", callback_data="delete_folder_current")],
                [InlineKeyboardButton("🗑️ Delete File", callback_data="delete_file_current")]
            ]
            await query.edit_message_text(f"✅ Folder '{folder_name}' deleted.", reply_markup=add_back_button(buttons))
        else:
            await query.answer("❌ Folder not found", show_alert=True)
        return

    # ---- DELETE FILE MENU ----
    if query.data == "delete_file_current" and is_admin:
        folder = get_current_folder(path) if path else folder_storage
        files = folder.get("files", {})
        if not files:
            await query.edit_message_text("⚠️ No files to delete.", reply_markup=add_back_button([]))
            return
        
        # Store mapping of short keys -> filenames
        file_map = {}
        buttons = []
        for idx, filename in enumerate(files.keys()):
            key = f"file{idx}"
            file_map[key] = filename
            buttons.append([InlineKeyboardButton(filename[:40], callback_data=f"delete_file_select|{key}")])  # show short name
        
        context.user_data["file_map"] = file_map
        await query.edit_message_text("🗑️ Select a file to delete:", reply_markup=add_back_button(buttons))
        return

    # ---- DELETE SELECTED FILE ----
    if query.data.startswith("delete_file_select|") and is_admin:
        key = query.data.split("|")[1]
        filename = context.user_data.get("file_map", {}).get(key)
        if not filename:
            await query.answer("❌ File not found", show_alert=True)
            return
    
        folder = get_current_folder(path) if path else folder_storage
        if filename in folder.get("files", {}):
            del folder["files"][filename]
            save_data()
            await query.edit_message_text(f"✅ File '{filename}' deleted.", reply_markup=add_back_button([]))
        else:
            await query.answer("❌ File not found", show_alert=True)
        return


# ===== HANDLE TEXT (FOLDER NAMES) =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username != ADMIN_USERNAME:
        return
    if context.user_data.get("awaiting_folder_name"):
        name = update.message.text.strip()
        path = context.user_data.get("folder_path", [])
        folder = get_current_folder(path) if path else folder_storage
        if name in folder.get("subfolders", {}):
            await update.message.reply_text("⚠️ Folder already exists.")
        else:
            folder.setdefault("subfolders", {})[name] = {"files": {}, "subfolders": {}}
            save_data()
            await update.message.reply_text(f"✅ Folder '{name}' created.")
        context.user_data["awaiting_folder_name"] = False


# ===== HANDLE FILE UPLOADS =====
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username != ADMIN_USERNAME:
        await update.message.reply_text("⛔ Not authorized to upload files.")
        return

    path = upload_context.get(user.id, user_paths.get(user.id, []))
    folder = get_current_folder(path) if path else folder_storage

    try:
        if update.message.document:
            file = update.message.document
            folder.setdefault("files", {})[file.file_name] = file.file_id  # JSON safe
            save_data()
            await update.message.reply_text(f"✅ File '{file.file_name}' uploaded.")
        elif update.message.photo:
            photo = update.message.photo[-1]
            filename = f"photo_{photo.file_unique_id}.jpg"
            folder.setdefault("files", {})[filename] = photo.file_id  # JSON safe
            save_data()
            await update.message.reply_text(f"✅ Photo saved as '{filename}'")
    except Exception as e:
        await update.message.reply_text(f"❌ Error uploading file: {e}")


# ===== MAIN =====
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    print("✅ File Manager Bot running safely with JSON-safe uploads...")
    app.run_polling()


if __name__ == "__main__":
    main()

