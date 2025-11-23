import os
import time
import threading
import logging
import datetime
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import reconocimiento_de_objetos as reconocimiento 
import notifications

# --- Cargar Variables de Entorno ---
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
MASTER_UID = os.getenv("MASTER_UID")
DOWNLOAD_FOLDER = os.getenv("DOWNLOAD_FOLDER", "imagenes_recibidas")
EMAIL_SENDER_ADDRESS = os.getenv("EMAIL_SENDER_ADDRESS")
EMAIL_SENDER_PASSWORD = os.getenv("EMAIL_SENDER_PASSWORD")
CORREO_ADMIN = os.getenv("CORREO_ADMIN")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

# --- Configuración Inicial ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
client = MongoClient(MONGO_URI)
db = client.Caja_de_Herramientas_Usuarios
users_collection = db.Lista_usuarios_niveles
incidents_collection = db.Registro_Incidencias
estado_bandejas_collection = db.Estado_Bandejas 

# --- Variables de Estado Global ---
session = {"state": "INACTIVE"}
admin_state = {} 
command_queue = []
telegram_app = None

# =================================================================================
# Lógica de Telegram y Callbacks
# =================================================================================

async def send_message(chat_id, message, reply_markup=None, photo_path=None):
    if telegram_app:
        try:
            if photo_path and os.path.exists(photo_path):
                await telegram_app.bot.send_photo(chat_id=chat_id, photo=open(photo_path, 'rb'), caption=message, reply_markup=reply_markup)
            else:
                await telegram_app.bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)
        except Exception as e:
            print(f"Error al enviar mensaje a {chat_id}: {e}")

async def admin_menu_callback(context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Anadir Usuario", callback_data='add_user')],
        [InlineKeyboardButton("Enlazar Usuario (Auto)", callback_data='link_user')],
        [InlineKeyboardButton("Enlazar ID Manualmente", callback_data='link_user_manual')],
        [InlineKeyboardButton("Controlar Bandeja 1", callback_data='toggle_tray_1')],
        [InlineKeyboardButton("Controlar Bandeja 2", callback_data='toggle_tray_2')],
        [InlineKeyboardButton("Salir del Modo Admin", callback_data='cancel_admin')]
    ]
    await send_message(ADMIN_CHAT_ID, "Modo Administrador Activado. Selecciona una opcion:", reply_markup=InlineKeyboardMarkup(keyboard))

async def checkin_timeout_callback(context: ContextTypes.DEFAULT_TYPE):
    global session
    job_data = context.job.data
    tray_id = job_data["tray_id"]
    user_chat_id = job_data["user_chat_id"]
    
    if session.get("state") == f"MULTI_CHECKIN_PENDIENTE_FOTO_{tray_id}" or session.get("state") == "ABIERTA_ESPERANDO_FOTO_INICIAL":
        print(f"Timeout de Check-in para Bandeja {tray_id}. Registrando incidencia.")
        incidents_collection.insert_one({
            "incidencia": "Falta foto de Check-in",
            "usuario_responsable": session.get("user"), "uid_responsable": session.get("uid"),
            "fecha_reporte": datetime.datetime.now(datetime.timezone.utc), "bandeja": int(tray_id)
        })
        session["state"] = "EN_USO"
        await send_message(user_chat_id, f"ALERTA: No se recibio la foto inicial para la Bandeja {tray_id} en 5 minutos. La sesion ha sido marcada para revision.")

# =================================================================================
# Handlers de Telegram
# =================================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global admin_state
    user_chat_id = str(update.message.chat_id)
    
    if admin_state.get("state") == "awaiting_user_start" and admin_state.get("user_to_link"):
        user_to_link = admin_state["user_to_link"]
        admin_state["linking_chat_id"] = user_chat_id
        await send_message(user_chat_id, "Gracias. Tu cuenta de Telegram esta lista para ser enlazada. El administrador debe completar el proceso.")
        await send_message(ADMIN_CHAT_ID, f"El usuario '{user_to_link['nombre']}' ha iniciado el enlace. Para confirmar, pasa la tarjeta RFID de '{user_to_link['nombre']}' por el lector.")
        admin_state["state"] = "awaiting_linking_card_scan"
    else:
        await update.message.reply_text(f"Hola, soy el bot de la caja de herramientas. Tu ID de chat es: {user_chat_id}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja la logica de auditoria de Check-in y Check-out usando el inventario dinamico.
    """
    global session
    user_chat_id = str(update.message.chat_id)

    if session.get("user_chat_id") != user_chat_id:
        await update.message.reply_text("No tienes una sesion de auditoria activa en este momento.")
        return

    current_state = session.get("state")
    tray_to_audit = None
    
    if current_state in ["ABIERTA_ESPERANDO_FOTO_INICIAL", "CERRANDO_ESPERANDO_FOTO_FINAL"]:
        tray_to_audit = session.get("active_tray")
    elif current_state in ["MULTI_CHECKIN_PENDIENTE_FOTO_1", "CERRANDO_ESPERANDO_FOTO_1"]:
        tray_to_audit = "1"
    elif current_state in ["MULTI_CHECKIN_PENDIENTE_FOTO_2", "CERRANDO_ESPERANDO_FOTO_2"]:
        tray_to_audit = "2"

    if not tray_to_audit:
        await update.message.reply_text("No estoy esperando ninguna foto en este momento.")
        return

    # Descarga y analiza la foto
    photo_file = await update.message.photo[-1].get_file()
    file_path = os.path.join(DOWNLOAD_FOLDER, f"bandeja_{tray_to_audit}_{photo_file.file_id}.jpg")
    await photo_file.download_to_drive(file_path)
    await update.message.reply_text(f"Foto de bandeja {tray_to_audit} recibida. Analizando...")

    reporte, _ = reconocimiento.analizar_inventario_ia(file_path, tray_to_audit)
    detected_tools = set(reporte.get("herramientas_detectadas", []))

    # --- Logica de estado para Check-in ---
    if current_state in ["ABIERTA_ESPERANDO_FOTO_INICIAL", "MULTI_CHECKIN_PENDIENTE_FOTO_1", "MULTI_CHECKIN_PENDIENTE_FOTO_2"]:
        job_name = f"checkin_timer_{tray_to_audit}"
        for job in context.job_queue.get_jobs_by_name(job_name): job.schedule_removal()

        # Carga el inventario esperado desde la DB (guardado en la sesion por 'handle_verification')
        inventario_esperado = set(session.get(f"inventario_esperado_checkin_{tray_to_audit}", []))
        
        # Compara la foto con el inventario esperado
        if detected_tools == inventario_esperado:
            await send_message(user_chat_id, f"Check-in de Bandeja {tray_to_audit} exitoso. El inventario coincide.")
        else:
            faltantes_al_inicio = list(inventario_esperado - detected_tools)
            encontradas_al_inicio = list(detected_tools - inventario_esperado)
            
            message = f"ATENCION (Bandeja {tray_to_audit}): Se detecto una discrepancia en el inventario inicial."
            if faltantes_al_inicio:
                message += f"\nFaltaban: {', '.join(faltantes_al_inicio)}."
                # Registrar incidencia para el turno anterior
                incidents_collection.insert_one({
                    "herramientas_faltantes": faltantes_al_inicio, "estado": "Faltante al Check-in",
                    "usuario_responsable": "Turno Anterior/Desconocido", "uid_responsable": "N/A",
                    "fecha_reporte": datetime.datetime.now(datetime.timezone.utc), "bandeja": int(tray_to_audit)
                })
            if encontradas_al_inicio:
                 message += f"\nSe encontraron herramientas extra: {', '.join(encontradas_al_inicio)}."
            
            message += "\nSe ha notificado al administrador. Tu sesion ha comenzado con el inventario actual."
            await send_message(user_chat_id, message)
            await send_message(ADMIN_CHAT_ID, f"ALERTA DE CHECK-IN (Usuario: {session.get('user')}):\n{message}")

        # Guarda el inventario REAL detectado como la base para esta sesion
        session[f"inventario_sesion_{tray_to_audit}"] = list(detected_tools)

        # Logica para avanzar en el flujo de check-in
        if current_state == "ABIERTA_ESPERANDO_FOTO_INICIAL":
            session["state"] = "EN_USO"
        elif current_state == "MULTI_CHECKIN_PENDIENTE_FOTO_1":
            session["state"] = "MULTI_CHECKIN_PENDIENTE_FOTO_2"
            await send_message(user_chat_id, f"Ahora, envia la foto de 'antes' para la BANDEJA 2.")
        elif current_state == "MULTI_CHECKIN_PENDIENTE_FOTO_2":
            session["state"] = "EN_USO"
            await send_message(user_chat_id, f"Check-in completado para ambas bandejas.")

    # --- Logica de estado para Check-out ---
    elif current_state in ["CERRANDO_ESPERANDO_FOTO_1", "CERRANDO_ESPERANDO_FOTO_2", "CERRANDO_ESPERANDO_FOTO_FINAL"]:
        # Compara la foto de 'despues' con el inventario que el usuario recibio al 'antes'
        inventario_de_sesion = set(session.get(f"inventario_sesion_{tray_to_audit}", []))
        
        faltantes_ahora = list(inventario_de_sesion - detected_tools)
        encontradas_ahora = list(detected_tools - inventario_de_sesion)

        if not faltantes_ahora and not encontradas_ahora:
            # Caso 1: Todo coincide perfectamente
            if session.get("is_multi_tray"):
                if current_state == "CERRANDO_ESPERANDO_FOTO_1":
                    session["state"] = "CERRANDO_ESPERANDO_FOTO_2"
                    await send_message(user_chat_id, f"Auditoria de Bandeja 1 correcta. Ahora, envia la foto de 'despues' para la BANDEJA 2.")
                elif current_state == "CERRANDO_ESPERANDO_FOTO_2":
                    session["state"] = "ESPERANDO_BLOQUEO_MANUAL"
                    keyboard = [[InlineKeyboardButton("Confirmar y Bloquear Bandejas", callback_data='lock_now')]]
                    await send_message(user_chat_id, f"Auditoria de Bandeja 2 correcta. Por favor, asegura que ambas bandejas esten cerradas y presiona el boton para bloquear.", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                session["state"] = "ESPERANDO_BLOQUEO_MANUAL"
                keyboard = [[InlineKeyboardButton("Confirmar y Bloquear Bandeja", callback_data='lock_now')]]
                await send_message(user_chat_id, f"Auditoria final correcta. Por favor, asegura que la bandeja este cerrada y presiona el boton para bloquear.", reply_markup=InlineKeyboardMarkup(keyboard))
        
        else:
            # Caso 2: El usuario encontro una herramienta que faltaba
            if encontradas_ahora:
                await send_message(user_chat_id, f"Gracias por devolver herramientas que faltaban: {', '.join(encontradas_ahora)}.")
                # Actualiza el inventario de referencia de la base de datos
                estado_bandejas_collection.update_one(
                    {"bandeja_id": int(tray_to_audit)},
                    {"$addToSet": {"inventario_actual_esperado": {"$each": encontradas_ahora}}}
                )
            
            # Caso 3: El usuario perdio una herramienta
            if faltantes_ahora:
                session["missing_tools"] = faltantes_ahora
                session["state"] = "AUDITORIA_FALLIDA_ESPERANDO_DECISION"
                keyboard = [[InlineKeyboardButton("Enviar Nueva Foto", callback_data=f'retry_photo_{tray_to_audit}')], [InlineKeyboardButton("Declarar Incidencia", callback_data=f'declare_incident_{tray_to_audit}')]]
                await send_message(user_chat_id, f"ALERTA: Discrepancia en Bandeja {tray_to_audit}. Faltan: {', '.join(faltantes_ahora)}.", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                # Si solo encontro herramientas pero no falta ninguna, permite el cierre
                session["state"] = "ESPERANDO_BLOQUEO_MANUAL" # Asume que el flujo multi-bandeja ya termino
                keyboard = [[InlineKeyboardButton("Confirmar y Bloquear Bandejas", callback_data='lock_now')]]
                await send_message(user_chat_id, f"Auditoria final correcta. Presiona el boton para bloquear.", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja TODAS las interacciones con los botones en linea enviados por el bot.
    """
    global admin_state, session
    
    query = update.callback_query
    await query.answer() # Responde al callback inmediatamente
    chat_id = str(query.message.chat_id)

    if session.get("state") == "AUDITORIA_FALLIDA_ESPERANDO_DECISION":
        if chat_id != session.get("user_chat_id"):
            await query.answer(text="Esta accion solo puede ser realizada por el usuario que inicio la sesion.", show_alert=True)
            return

        tray_id = query.data.split('_')[-1]
        
        # Borra el mensaje con la foto y los botones de incidencia
        await query.delete_message()

        if query.data.startswith('retry_photo_'):
            # --- CORRECCIÓN DEL BUG DE REINTENTO ---
            # Restaura el estado de "espera de foto" correcto
            if not session.get("is_multi_tray"):
                session["state"] = "CERRANDO_ESPERANDO_FOTO_FINAL"
            else:
                if tray_id == "1":
                    session["state"] = "CERRANDO_ESPERANDO_FOTO_1"
                elif tray_id == "2":
                    session["state"] = "CERRANDO_ESPERANDO_FOTO_2"
            
            await send_message(chat_id, f"Entendido. Por favor, envia la nueva foto para la revalidacion de la Bandeja {tray_id}.")
            
        elif query.data.startswith('declare_incident_'):
            missing_tools = session.get("missing_tools", [])
            
            if missing_tools:
                # 1. Registrar en la base de datos
                incidents_collection.insert_one({
                    "herramientas_faltantes": missing_tools,
                    "estado": "Extraviada/Danada",
                    "usuario_responsable": session.get("user"),
                    "uid_responsable": session.get("uid"),
                    "fecha_reporte": datetime.datetime.now(datetime.timezone.utc),
                    "bandeja": int(tray_id)
                })

                # 2. Preparar y enviar notificaciones
                subject = f"Alerta de Incidencia en Bandeja {tray_id}"
                body = (f"Se ha reportado una incidencia para la(s) siguiente(s) herramienta(s):\n- {', '.join(missing_tools)}\n\n"
                        f"Usuario responsable: {session.get('user')}\nFecha del reporte: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                telegram_app.job_queue.run_once(lambda ctx: send_message(ADMIN_CHAT_ID, f"INCIDENCIA REGISTRADA:\n{body}"), 0)
                try:
                    notifications.send_incident_email(subject, body, CORREO_ADMIN, EMAIL_SENDER_ADDRESS, EMAIL_SENDER_PASSWORD, SMTP_SERVER, SMTP_PORT)
                    print("Notificacion de incidencia enviada por correo exitosamente.")
                except Exception as e:
                    print(f"Error al enviar correo de incidencia: {e}")

            # 3. Continuar con el flujo de cierre
            # Comprobar si era la ultima bandeja
            if not session.get("is_multi_tray") or (session.get("is_multi_tray") and tray_id == "2"):
                session["state"] = "ESPERANDO_BLOQUEO_MANUAL"
                keyboard = [[InlineKeyboardButton("Confirmar y Bloquear Bandeja(s)", callback_data='lock_now')]]
                await send_message(chat_id, f"Incidencia registrada para: {', '.join(missing_tools)}. Gracias. Ahora puedes terminar de cerrar la(s) bandeja(s) y presionar el boton para bloquear.", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                # Era la bandeja 1 de 2, pedimos la foto de la bandeja 2
                session["state"] = "CERRANDO_ESPERANDO_FOTO_2"
                await send_message(chat_id, f"Incidencia registrada para Bandeja 1. Ahora, por favor envia la foto de 'despues' para la BANDEJA 2.")
        
        return
    
    if query.data == 'lock_now':
        if session.get("state") == "ESPERANDO_BLOQUEO_MANUAL" and chat_id == session.get("user_chat_id"):
            command_queue.append({"command": "close_all"})
            session["state"] = "BLOQUEANDO" 
            await query.edit_message_text("Comando de bloqueo enviado. Esperando confirmacion del sistema...")
        else:
            await query.answer("Esta accion ya no es valida.", show_alert=True)
        return
    
    if chat_id != ADMIN_CHAT_ID:
        return

    # --- Logica del Menu de Administrador ---
    if query.data == 'add_user':
        await query.edit_message_text(text="Entendido. Por favor, escribe el nombre completo del nuevo usuario.")
        admin_state[chat_id] = {'state': 'awaiting_new_user_name'}

    elif query.data == 'link_user':
        unlinked_users = list(users_collection.find({"telegram_chat_id": {"$in": [None, ""]}}))
        if not unlinked_users:
            await query.edit_message_text("No hay usuarios pendientes de enlazar.")
            return
        keyboard = [[InlineKeyboardButton(user['nombre'], callback_data=f"link_{user['rfid_uid']}")] for user in unlinked_users]
        await query.edit_message_text("Enlace Automatico: Selecciona el usuario a enlazar:", reply_markup=InlineKeyboardMarkup(keyboard))
        admin_state[chat_id] = {"state": "selecting_user_to_link"}

    elif query.data == 'link_user_manual':
        unlinked_users = list(users_collection.find({"telegram_chat_id": {"$in": [None, ""]}}))
        if not unlinked_users:
            await query.edit_message_text("No hay usuarios pendientes de enlazar.")
            return
        keyboard = [[InlineKeyboardButton(user['nombre'], callback_data=f"manual_link_{user['rfid_uid']}")] for user in unlinked_users]
        await query.edit_message_text("Enlace Manual: Selecciona el usuario:", reply_markup=InlineKeyboardMarkup(keyboard))
        admin_state[chat_id] = {"state": "selecting_user_for_manual_link"}
        
    elif query.data.startswith('toggle_tray_'):
        tray_id = query.data.split('_')[-1]
        current_state_key = f"admin_tray_{tray_id}_state"
        if session.get(current_state_key, "BLOQUEADA") == "BLOQUEADA":
            command_queue.append({'command': 'open', 'tray': int(tray_id)})
            session[current_state_key] = 'ABIERTA'
            await query.answer(text=f"Comando enviado: Abrir Bandeja {tray_id}") # Notificacion temporal
        else:
            command_queue.append({'command': 'close', 'tray': int(tray_id)})
            session[current_state_key] = 'BLOQUEADA'
            await query.answer(text=f"Comando enviado: Cerrar Bandeja {tray_id}") # Notificacion temporal
    
    elif query.data == 'cancel_admin':
        await query.edit_message_text(text="Modo Administrador finalizado.")
        admin_state.pop(chat_id, None)

    # --- Logica del Flujo de Enlace de Cuentas (Auto) ---
    elif query.data.startswith('link_'):
        if admin_state.get(chat_id, {}).get("state") == "selecting_user_to_link":
            rfid_uid_to_link = query.data.split('_')[1]
            user = users_collection.find_one({"rfid_uid": rfid_uid_to_link})
            if user:
                admin_state[chat_id] = {"state": "awaiting_user_start", "user_to_link": user}
                await query.edit_message_text(f"Perfecto. Por favor, dile a '{user['nombre']}' que abra un chat conmigo y me envie el comando /start.")

    # --- Logica del Flujo de Enlace de Cuentas (Manual) ---
    elif query.data.startswith('manual_link_'):
        if admin_state.get(chat_id, {}).get("state") == "selecting_user_for_manual_link":
            rfid_uid_to_link = query.data.split('_')[-1]
            user = users_collection.find_one({"rfid_uid": rfid_uid_to_link})
            if user:
                admin_state[chat_id] = {"state": "awaiting_manual_chat_id", "user_to_link": user}
                await query.edit_message_text(f"Usuario '{user['nombre']}' seleccionado. Ahora, por favor, envia el Chat ID numerico del usuario.")

    # --- Logica del Proceso de Registro de Usuario ---
    elif query.data.startswith('perm_'):
        user_data = admin_state.get(chat_id, {})
        if user_data.get('state') == 'awaiting_new_user_permissions':
            permissions = []
            if query.data == 'perm_1': permissions = [1]
            elif query.data == 'perm_2': permissions = [2]
            elif query.data == 'perm_both': permissions = [1, 2]
            
            user_data['permissions'] = permissions
            user_data['state'] = 'awaiting_new_user_uid'
            admin_state[chat_id] = user_data
            await query.edit_message_text(
                text="Permisos guardados. Ahora, por favor, pasa la nueva tarjeta del usuario por el lector RFID para finalizar."
            )
            
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global admin_state
    chat_id = str(update.message.chat_id)
    
    current_state_info = admin_state.get(chat_id)
    
    if isinstance(current_state_info, dict):
        state = current_state_info.get('state')
        
        if state == 'awaiting_new_user_name':
            new_user_name = update.message.text
            admin_state[chat_id] = {'state': 'awaiting_new_user_permissions', 'name': new_user_name}
            keyboard = [
                [InlineKeyboardButton("Bandeja 1", callback_data='perm_1')],
                [InlineKeyboardButton("Bandeja 2", callback_data='perm_2')],
                [InlineKeyboardButton("Ambas Bandejas", callback_data='perm_both')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Nombre guardado: '{new_user_name}'. Ahora selecciona los permisos:", reply_markup=reply_markup)

        elif state == 'awaiting_manual_chat_id':
            manual_chat_id = update.message.text
            user_to_link = current_state_info.get("user_to_link")

            if manual_chat_id.isdigit() and user_to_link:
                users_collection.update_one(
                    {"rfid_uid": user_to_link["rfid_uid"]},
                    {"$set": {"telegram_chat_id": manual_chat_id}}
                )
                await send_message(ADMIN_CHAT_ID, f"Exito. El usuario '{user_to_link['nombre']}' ha sido enlazado manualmente al Chat ID {manual_chat_id}.")
                await send_message(manual_chat_id, "Tu cuenta ha sido enlazada al sistema por un administrador.")
                admin_state.pop(chat_id, None)
            else:
                await update.message.reply_text("Entrada invalida. Por favor, envia solo el Chat ID numerico. Proceso cancelado.")
                admin_state.pop(chat_id, None)

# =================================================================================
# Lógica del Servidor Web (Flask)
# =================================================================================
flask_app = Flask(__name__)

@flask_app.route('/report_event', methods=['POST'])
def handle_pico_event():
    global session
    data = request.get_json()
    if not data: return jsonify({"status": "error", "message": "No data received"}), 400

    event = data.get("event")
    print(f"Evento recibido del Pico: {event}")
    user_chat_id = session.get("user_chat_id")
    if not user_chat_id: return jsonify({"status": "no_active_session"})

    # --- Logica de Check-out ---
    if session.get("state") == "EN_USO":
        if event == "inicio_cierre_1" and session.get("is_multi_tray"):
            session["state"] = "CERRANDO_ESPERANDO_FOTO_1"
            telegram_app.job_queue.run_once(lambda ctx: send_message(user_chat_id, "Detectado intento de cierre de Bandeja 1. Por favor, envia la foto de 'check-out'."), 0)
        
        elif not session.get("is_multi_tray") and event == f"inicio_cierre_{session.get('active_tray')}":
            session["state"] = "CERRANDO_ESPERANDO_FOTO_FINAL"
            telegram_app.job_queue.run_once(lambda ctx: send_message(user_chat_id, f"Detectado intento de cierre de Bandeja {session.get('active_tray')}. Por favor, envia la foto de 'check-out'."), 0)

    # --- Logica de Cierre Final ---
    elif event == "cierre_exitoso_final":
        message = "Bandeja(s) cerrada(s) y bloqueada(s) de forma segura."
        telegram_app.job_queue.run_once(lambda ctx: send_message(user_chat_id, message), 0)
        print("DEBUG: Reseteando la sesion a INACTIVE.")
        session = {"state": "INACTIVE"}

    return jsonify({"status": "event_received"})

@flask_app.route('/verificar_rfid', methods=['POST'])
def handle_verification():
    """
    Punto de entrada principal para las verificaciones de RFID.
    """
    start_time = time.time()
    global session, admin_state
    uid = request.get_json().get('uid')
    print(f"Peticion de verificacion recibida. UID: {uid}")
    
    chat_id = ADMIN_CHAT_ID
    current_admin_state = admin_state.get(chat_id)

    # --- Flujo 1: Finalizar el enlace de una cuenta de usuario ---
    if isinstance(current_admin_state, dict) and current_admin_state.get("state") == "awaiting_linking_card_scan":
        user_to_link = current_admin_state.get("user_to_link", {})
        if uid == user_to_link.get("rfid_uid"):
            linking_chat_id = current_admin_state.get("linking_chat_id")
            users_collection.update_one({"rfid_uid": uid}, {"$set": {"telegram_chat_id": linking_chat_id}})
            message = f"Confirmado. La cuenta de '{user_to_link['nombre']}' ha sido enlazada."
            telegram_app.job_queue.run_once(lambda ctx: send_message(ADMIN_CHAT_ID, message), 0)
            telegram_app.job_queue.run_once(lambda ctx: send_message(linking_chat_id, "Tu cuenta ha sido enlazada con exito."), 0)
            admin_state.pop(chat_id, None)
            return jsonify({"status": "linking_complete"})
        else:
            telegram_app.job_queue.run_once(lambda ctx: send_message(ADMIN_CHAT_ID, "Tarjeta incorrecta. El proceso de enlace ha sido cancelado."), 0)
            admin_state.pop(chat_id, None)
            return jsonify({"status": "linking_failed"})

    # --- Flujo 2: Finalizar el registro de un nuevo usuario ---
    if isinstance(current_admin_state, dict) and current_admin_state.get('state') == 'awaiting_new_user_uid':
        if users_collection.find_one({"rfid_uid": uid}):
            message = f"ERROR: La tarjeta con UID {uid} ya esta registrada."
        else:
            new_user = {"rfid_uid": uid, "nombre": current_admin_state['name'], "permisos": current_admin_state['permissions'], "telegram_chat_id": ""}
            users_collection.insert_one(new_user)
            message = f"Exito. El usuario '{current_admin_state['name']}' ha sido registrado con la tarjeta UID {uid}."
        telegram_app.job_queue.run_once(lambda ctx: send_message(ADMIN_CHAT_ID, message), 0)
        admin_state.pop(chat_id, None)
        return jsonify({"status": "registration_complete"})

    # --- Flujo 3: Detección de Tarjeta Maestra ---
    if uid == MASTER_UID:
        print("TARJETA MAESTRA DETECTADA")
        telegram_app.job_queue.run_once(admin_menu_callback, 0)
        
        #### Calculo de latencia ####
        end_time = time.time()
        latency_ms = (end_time - start_time) * 1000
        print(f"--- LATENCIA (RFID a Bot - Admin): {latency_ms:.0f} ms ---")
        #### FIN calculo de latencia ####

        return jsonify({"status": "master_mode"})
    
    user = users_collection.find_one({"rfid_uid": uid})
    if user and session.get("state") in ["INACTIVE", "BLOQUEADA"]:
        user_chat_id = user.get("telegram_chat_id")
        if not user_chat_id:
            message = f"Alerta: El usuario '{user['nombre']}' intento abrir una bandeja pero no tiene una cuenta de Telegram enlazada."
            telegram_app.job_queue.run_once(lambda ctx: send_message(ADMIN_CHAT_ID, message), 0)

            #### Calculo de latencia intento de ingreso fallido ####
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000
            print(f"--- LATENCIA (RFID a Bot - Alerta): {latency_ms:.0f} ms ---")
            #### FIN calculo de latencia ####

            return jsonify({"status": "acceso_denegado", "reason": "no_telegram_link"})

        permisos = user.get('permisos', [])
        if not isinstance(permisos, list): permisos = [permisos]
        
        # --- NUEVA LOGICA: Cargar el inventario dinamico esperado ---
        inventario_esperado_1 = estado_bandejas_collection.find_one({"bandeja_id": 1}).get("inventario_actual_esperado", [])
        inventario_esperado_2 = estado_bandejas_collection.find_one({"bandeja_id": 2}).get("inventario_actual_esperado", [])
        
        if sorted(permisos) == [1, 2]:
            command_queue.extend([{"command": "open", "tray": 1}, {"command": "open", "tray": 2}])
            session = {
                "state": "MULTI_CHECKIN_PENDIENTE_FOTO_1", "user": user.get("nombre"), "uid": uid, 
                "user_chat_id": user_chat_id, "is_multi_tray": True,
                "inventario_esperado_checkin_1": inventario_esperado_1,
                "inventario_esperado_checkin_2": inventario_esperado_2
            }
            telegram_app.job_queue.run_once(lambda ctx: send_message(user_chat_id, f"Hola, {user.get('nombre')}. Abriendo ambas bandejas. Por favor, envia la foto de 'antes' para la BANDEJA 1."), 0)
            return jsonify({"status": "acceso_concedido"})
        
        elif permisos:
            tray_id = str(permisos[0])
            inventario_esperado = inventario_esperado_1 if tray_id == "1" else inventario_esperado_2
            
            command_queue.append({"command": "open", "tray": int(tray_id)})
            session = {
                "state": "ABIERTA_ESPERANDO_FOTO_INICIAL", "user": user.get("nombre"), "uid": uid, 
                "user_chat_id": user_chat_id, "active_tray": tray_id, "is_multi_tray": False,
                f"inventario_esperado_checkin_{tray_id}": inventario_esperado
            }
            telegram_app.job_queue.run_once(checkin_timeout_callback, 300, data={"tray_id": tray_id, "user_chat_id": user_chat_id}, name=f"checkin_timer_{tray_id}")
            telegram_app.job_queue.run_once(lambda ctx: send_message(user_chat_id, f"Hola, {user.get('nombre')}. Abriendo Bandeja {tray_id}. Tienes 5 minutos para enviar la foto de 'antes'."), 0)
            return jsonify({"status": "acceso_concedido"})

    print("Acceso denegado (Usuario no encontrado, sin permisos, o sesion ya activa).")
    return jsonify({"status": "acceso_denegado"})

@flask_app.route('/poll_command', methods=['GET'])
def poll_command():
    if command_queue:
        return jsonify(command_queue.pop(0))
    return jsonify({})

# =================================================================================
# Función Principal e Inicialización
# =================================================================================

def inicializar_estado_bandejas():
    """Verifica y crea el estado inicial de las bandejas en la DB si no existe."""
    print("Inicializando el estado de las bandejas...")
    try:
        # Carga las listas maestras desde el modulo de reconocimiento
        lista_maestra_1 = set(reconocimiento.INVENTARIO_BANDEJA_1)
        lista_maestra_2 = set(reconocimiento.INVENTARIO_BANDEJA_2)
    except Exception as e:
        print(f"ERROR: No se pudieron cargar las listas de inventario desde 'reconocimiento_de_objetos.py'. Verifica que las listas INVENTARIO_BANDEJA_1 y 2 existan. Error: {e}")
        return

    # Verifica Bandeja 1
    if not estado_bandejas_collection.find_one({"bandeja_id": 1}):
        print("Creando documento de estado para Bandeja 1...")
        estado_bandejas_collection.insert_one({
            "bandeja_id": 1,
            "inventario_actual_esperado": list(lista_maestra_1)
        })
    
    # Verifica Bandeja 2
    if not estado_bandejas_collection.find_one({"bandeja_id": 2}):
        print("Creando documento de estado para Bandeja 2...")
        estado_bandejas_collection.insert_one({
            "bandeja_id": 2,
            "inventario_actual_esperado": list(lista_maestra_2)
        })
    print("Estado de las bandejas verificado y listo.")

def run_telegram_bot():
    global telegram_app
    telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    telegram_app.add_handler(CommandHandler('start', start_command))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("Bot de Telegram iniciado y escuchando...")
    telegram_app.run_polling()

if __name__ == '__main__':
    # Inicializa el estado de las bandejas en la DB ANTES de iniciar los hilos
    inicializar_estado_bandejas()
    
    telegram_thread = threading.Thread(target=run_telegram_bot)
    telegram_thread.daemon = True
    telegram_thread.start()
    
    print("Servidor Flask iniciado...")
    flask_app.run(host='0.0.0.0', port=5000, debug=False)