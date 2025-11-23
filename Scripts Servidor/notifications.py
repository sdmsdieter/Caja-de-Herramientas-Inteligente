import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_incident_email(subject, body, receiver_email, sender_email, sender_password, smtp_server, smtp_port):
    """
    Envia un correo electronico de notificacion de incidencia usando SMTP.
    """
    try:
        # Crea el objeto del mensaje
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = receiver_email
        message["Subject"] = subject
        
        # Adjunta el cuerpo del correo como texto plano
        message.attach(MIMEText(body, "plain"))
        
        # Inicia la conexion con el servidor SMTP (en este caso, Gmail)
        print(f"Conectando al servidor SMTP {smtp_server} en el puerto {smtp_port}...")
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()  # Inicia una conexion segura
        
        # Inicia sesion en la cuenta de correo
        print(f"Iniciando sesion como {sender_email}...")
        server.login(sender_email, sender_password)
        
        # Envia el correo
        text = message.as_string()
        server.sendmail(sender_email, receiver_email, text)
        print("Correo de notificacion enviado exitosamente.")
        
    except Exception as e:
        # Imprime un error detallado si algo falla
        print(f"Error al enviar el correo de notificacion: {e}")
        
    finally:
        # Asegura que la conexion con el servidor se cierre
        if 'server' in locals() and server:
            server.quit()

